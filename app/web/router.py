"""Веб-интерфейс: HTML-страницы и фрагменты для HTMX."""

import json
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import pc_service
from app.core.auth import (
    authenticate_user,
    create_session,
    delete_session_cookie,
    get_current_user,
    set_session_cookie,
)
from app.core.mqtt_client import MqttService
from app.core.pc_service import WEEKDAY_NAMES  # к импортам
from app.database import get_db
from app.deps import get_mqtt_service
from app.models import PC, Session, User

DAYS_OF_WEEK = list(zip(WEEKDAY_NAMES, ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], strict=True))


BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["web"])


def _with_toast(response: HTMLResponse, message: str, toast_type: str = "success") -> HTMLResponse:
    """Добавляет HX-Trigger для показа тоста на клиенте."""
    response.headers["HX-Trigger"] = json.dumps({"show-toast": {"message": message, "type": toast_type}})
    return response


def _opt_int(raw: str, fallback: int | None = None) -> int | None:
    """Парсит необязательное число из формы (пусто → fallback)."""
    raw = raw.strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return max(0, min(value, 1440))


async def _history_context(db: AsyncSession, pc: PC, fetch, offset: int = 0, limit: int = 20) -> dict:
    """Контекст для таблиц событий/команд с дозагрузкой."""
    items = await fetch(pc.id, offset + limit + 1)
    return {
        "pc": pc,
        "rows": items[: offset + limit],
        "offset": offset,
        "limit": limit,
        "has_more": len(items) > offset + limit,
    }


# === Jinja-фильтры форматирования ===


def _fmt_duration(seconds: int) -> str:
    """3660 -> '1 ч 01 мин'."""
    seconds = int(seconds or 0)
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    return f"{minutes} мин"


def _fmt_dt(value: datetime | None) -> str:
    """Datetime -> '21.08.2026 14:30' в таймзоне сервера."""
    if value is None:
        return "—"
    tz = ZoneInfo(get_settings().server_timezone)
    return value.astimezone(tz).strftime("%d.%m.%Y %H:%M")


templates.env.filters["fmt_duration"] = _fmt_duration
templates.env.filters["fmt_dt"] = _fmt_dt


# === Авторизация для страниц ===


class LoginRequiredError(Exception):
    """Запрос к защищённой странице без сессии."""


async def get_web_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    try:
        return await get_current_user(request, db)
    except HTTPException:
        raise LoginRequiredError from None


WebUser = Annotated[User, Depends(get_web_user)]
WebDb = Annotated[AsyncSession, Depends(get_db)]
MqttDep = Annotated[MqttService, Depends(get_mqtt_service)]


# === Хелперы ===


async def _get_pc_or_404(db: AsyncSession, pc_name: str) -> PC:
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")
    return pc


async def _dashboard_context(db: AsyncSession) -> dict:
    """Собирает данные для дашборда: ПК + использование + статистика."""
    settings = get_settings()
    result = await db.scalars(select(PC).where(PC.is_active.is_(True)).order_by(PC.id))
    pcs = result.all()

    items = [await pc_service.get_pc_with_usage_today(db, settings, pc) for pc in pcs]

    stats = {
        "total": len(items),
        "online": sum(1 for i in items if i["pc"].is_online),
        "locked": sum(1 for i in items if i["pc"].is_locked),
        "active_today": sum(i["usage_today"]["active_seconds"] for i in items),
    }
    return {"items": items, "stats": stats}


async def _grid_response(request: Request, db: AsyncSession) -> HTMLResponse:
    """Возвращает фрагмент сетки ПК (для HTMX-обновления)."""
    ctx = await _dashboard_context(db)
    return templates.TemplateResponse(request, "partials/pc_grid.html", ctx)


# === Страницы ===


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    db: WebDb,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    user = await authenticate_user(db, username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Неверный логин или пароль"}, status_code=401
        )

    session = await create_session(
        db,
        user,
        ip_address=request.client.host,
        user_agent=request.headers.get("user-agent"),
    )
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, session.id)
    return response


@router.post("/logout")
async def logout_submit(request: Request, db: WebDb, user: WebUser):
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        session = await db.scalar(select(Session).where(Session.id == session_id))
        if session:
            await db.delete(session)
            await db.commit()

    response = RedirectResponse("/login", status_code=303)
    delete_session_cookie(response)
    return response


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: WebUser, db: WebDb):
    ctx = await _dashboard_context(db)
    return templates.TemplateResponse(request, "dashboard.html", {"user": user, **ctx})


# === HTMX-фрагменты и действия ===


@router.get("/web/partials/pc-grid", response_class=HTMLResponse)
async def pc_grid_partial(request: Request, user: WebUser, db: WebDb):
    """Фрагмент для автообновления дашборда каждые 30 секунд."""
    return await _grid_response(request, db)


@router.post("/web/pcs/{pc_name}/lock", response_class=HTMLResponse)
async def web_lock(pc_name: str, request: Request, user: WebUser, db: WebDb, mqtt: MqttDep):
    pc = await _get_pc_or_404(db, pc_name)
    await pc_service.lock_pc(db, mqtt, get_settings(), pc, reason="manual", delay_seconds=0)
    await db.commit()
    response = await _grid_response(request, db)
    return _with_toast(response, f"{pc.display_name}: Команда блокировки отправлена")


@router.post("/web/pcs/{pc_name}/unlock", response_class=HTMLResponse)
async def web_unlock(pc_name: str, request: Request, user: WebUser, db: WebDb, mqtt: MqttDep):
    pc = await _get_pc_or_404(db, pc_name)
    await pc_service.unlock_pc(db, mqtt, get_settings(), pc, reason="manual")
    await db.commit()
    response = await _grid_response(request, db)
    return _with_toast(response, f"{pc.display_name}: Команда разблокировки отправлена")


@router.post("/web/pcs/{pc_name}/add-time", response_class=HTMLResponse)
async def web_add_time(
    pc_name: str,
    request: Request,
    user: WebUser,
    db: WebDb,
    mqtt: MqttDep,
    minutes: Annotated[int, Form(ge=1, le=600)] = 30,
):
    pc = await _get_pc_or_404(db, pc_name)
    await pc_service.add_time(db, mqtt, get_settings(), pc, minutes=minutes)
    await db.commit()
    response = await _grid_response(request, db)
    return _with_toast(response, f"{pc.display_name}: +{minutes} мин")


@router.get("/pcs/{pc_name}", response_class=HTMLResponse)
async def pc_page(pc_name: str, request: Request, user: WebUser, db: WebDb):
    """Страница ПК: статус, график, настройки, расписание, журналы."""
    settings = get_settings()
    pc = await _get_pc_or_404(db, pc_name)

    data = await pc_service.get_pc_with_usage_today(db, settings, pc)
    usage_period = await pc_service.get_usage_period(db, settings, pc.id, days=7)
    schedule = await pc_service.get_pc_schedule(db, pc.id)

    max_seconds = (
        max(
            [u.active_seconds for u in usage_period] + [data["usage_today"]["limit_seconds"]],
            default=1,
        )
        or 1
    )
    chart = [
        {
            "label": u.usage_date.strftime("%d.%m"),
            "seconds": u.active_seconds,
            "percent": max(2, round(u.active_seconds / max_seconds * 100)),
        }
        for u in usage_period
    ]

    return templates.TemplateResponse(
        request,
        "pc.html",
        {
            "user": user,
            "pc": pc,
            "usage": data["usage_today"],
            "chart": chart,
            "schedule": schedule,
            "events_ctx": await _history_context(db, pc, partial(pc_service.get_pc_events, db)),
            "commands_ctx": await _history_context(db, pc, partial(pc_service.get_pc_commands, db)),
            "days_of_week": DAYS_OF_WEEK,
            "toast": request.query_params.get("toast"),
        },
    )


@router.post("/web/pcs/{pc_name}/settings")
async def web_update_settings(
    pc_name: str,
    request: Request,
    user: WebUser,
    db: WebDb,
    display_name: Annotated[str, Form()],
    daily_limit_minutes: Annotated[str, Form()],
    warning_before_lock_seconds: Annotated[str, Form()],
    is_active: Annotated[bool | None, Form()] = None,
    limit_mon: Annotated[str, Form()] = "",
    limit_tue: Annotated[str, Form()] = "",
    limit_wed: Annotated[str, Form()] = "",
    limit_thu: Annotated[str, Form()] = "",
    limit_fri: Annotated[str, Form()] = "",
    limit_sat: Annotated[str, Form()] = "",
    limit_sun: Annotated[str, Form()] = "",
):
    """Сохранение настроек ПК, включая лимиты по дням недели."""
    pc = await _get_pc_or_404(db, pc_name)

    pc.display_name = display_name.strip() or pc.display_name
    pc.daily_limit_minutes = _opt_int(daily_limit_minutes, pc.daily_limit_minutes) or pc.daily_limit_minutes
    pc.warning_before_lock_seconds = _opt_int(warning_before_lock_seconds, pc.warning_before_lock_seconds) or 0
    pc.is_active = bool(is_active)

    limits = {}
    raw_limits = [limit_mon, limit_tue, limit_wed, limit_thu, limit_fri, limit_sat, limit_sun]
    for day, raw in zip(WEEKDAY_NAMES, raw_limits, strict=True):
        value = _opt_int(raw)
        if value is not None:
            limits[day] = value
    pc.day_limits = limits

    await db.commit()
    return RedirectResponse(f"/pcs/{pc_name}?toast=settings_saved", status_code=303)


@router.get("/web/pcs/{pc_name}/partials/events", response_class=HTMLResponse)
async def events_partial(
    pc_name: str,
    request: Request,
    user: WebUser,
    db: WebDb,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=5, le=100)] = 20,
):
    pc = await _get_pc_or_404(db, pc_name)
    ctx = await _history_context(db, pc, partial(pc_service.get_pc_events, db), offset, limit)
    return templates.TemplateResponse(request, "partials/events_table.html", ctx)


@router.get("/web/pcs/{pc_name}/partials/commands", response_class=HTMLResponse)
async def commands_partial(
    pc_name: str,
    request: Request,
    user: WebUser,
    db: WebDb,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=5, le=100)] = 20,
):
    pc = await _get_pc_or_404(db, pc_name)
    ctx = await _history_context(db, pc, partial(pc_service.get_pc_commands, db), offset, limit)
    return templates.TemplateResponse(request, "partials/commands_table.html", ctx)
