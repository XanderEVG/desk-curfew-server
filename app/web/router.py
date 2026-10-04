"""Веб-интерфейс: HTML-страницы и фрагменты для HTMX."""

from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
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
from app.database import get_db
from app.deps import get_mqtt_service
from app.models import PC, Session, User

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["web"])


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
    return await _grid_response(request, db)


@router.post("/web/pcs/{pc_name}/unlock", response_class=HTMLResponse)
async def web_unlock(pc_name: str, request: Request, user: WebUser, db: WebDb, mqtt: MqttDep):
    pc = await _get_pc_or_404(db, pc_name)
    await pc_service.unlock_pc(db, mqtt, get_settings(), pc, reason="manual")
    await db.commit()
    return await _grid_response(request, db)


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
    return await _grid_response(request, db)
