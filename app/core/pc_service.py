from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import PC, CommandLog, PcEvent, ScheduleSlot, UsageDaily

if TYPE_CHECKING:
    from app.core.agent_transport import AgentTransport

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


async def get_pc_by_name(session: AsyncSession, pc_name: str) -> PC | None:
    return await session.scalar(select(PC).where(PC.name == pc_name))


async def get_usage_today(
    session: AsyncSession,
    settings: Settings,
    pc_id: int,
) -> UsageDaily | None:
    today = datetime.now(UTC).astimezone(ZoneInfo(settings.server_timezone)).date()

    return await session.scalar(
        select(UsageDaily).where(
            UsageDaily.pc_id == pc_id,
            UsageDaily.usage_date == today,
        )
    )


async def get_or_create_usage_today(
    session: AsyncSession,
    settings: Settings,
    pc_id: int,
) -> UsageDaily:
    usage = await get_usage_today(session, settings, pc_id)

    if usage is None:
        today = datetime.now(UTC).astimezone(ZoneInfo(settings.server_timezone)).date()

        usage = UsageDaily(
            pc_id=pc_id,
            usage_date=today,
            active_seconds=0,
            locked_seconds=0,
        )
        session.add(usage)
        await session.flush()

    return usage


def get_end_of_day(settings: Settings, now_utc: datetime | None = None) -> datetime:
    """Возвращает конец текущего дня (00:00 следующего дня) в таймзоне сервера, в UTC."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    tz = ZoneInfo(settings.server_timezone)
    local_now = now_utc.astimezone(tz)

    next_day_start = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return next_day_start.astimezone(timezone.utc)


def get_limit_minutes_for_day(pc: PC, weekday: str) -> int:
    """Лимит минут для дня недели; фолбэк — daily_limit_minutes."""
    limits = pc.day_limits or {}
    value = limits.get(weekday)
    return int(value) if value is not None else pc.daily_limit_minutes


async def handle_heartbeat(
    session: AsyncSession,
    transport: AgentTransport,
    settings: Settings,
    pc_name: str,
    payload: dict[str, Any],
) -> None:
    pc = await get_pc_by_name(session, pc_name)
    if pc is None:
        logger.warning("Heartbeat from unknown PC: %s", pc_name)
        return

    now = datetime.now(UTC)
    pc.is_online = True
    pc.last_seen_at = now
    active_user = payload.get("active_user")
    if active_user:
        pc.last_active_user = str(active_user)

    locked = bool(payload.get("locked", pc.is_locked))
    pc.is_locked = locked
    if payload.get("lock_reason") is not None:
        pc.lock_reason = str(payload.get("lock_reason"))

    idle_seconds = _clamp_int(payload.get("idle_seconds"), 0, 0, 86400)
    pc.is_idle = idle_seconds >= settings.idle_threshold_seconds

    usage = await get_or_create_usage_today(session, settings, pc.id)
    if locked:
        usage.locked_seconds += settings.heartbeat_interval
    elif pc.is_idle:
        usage.idle_seconds += settings.heartbeat_interval
    else:
        usage.active_seconds += settings.heartbeat_interval

    # Агент жив, но не заблокирован, хотя сервер хочет блокировку —
    # повторяем команду сразу (троттлинг 120 с, как в планировщике).
    if (
        pc.desired_locked
        and not locked
        and (pc.last_command_at is None or (now - pc.last_command_at).total_seconds() > 120)
    ):
        await lock_pc(
            session,
            transport,
            settings,
            pc,
            reason=pc.desired_lock_reason or "schedule",
            delay_seconds=0,
        )


async def handle_status(
    session: AsyncSession,
    pc_name: str,
    payload: dict[str, Any],
) -> None:
    pc = await get_pc_by_name(session, pc_name)
    if pc is None:
        logger.warning("Status from unknown PC: %s", pc_name)
        return

    now = datetime.now(UTC)

    pc.is_online = bool(payload.get("online", True))

    if pc.is_online:
        pc.last_seen_at = now

    pc.is_locked = bool(payload.get("locked", False))

    if payload.get("lock_reason") is not None:
        pc.lock_reason = str(payload.get("lock_reason"))

    user = payload.get("user")
    if user:
        pc.last_active_user = str(user)


async def handle_event(
    session: AsyncSession,
    pc_name: str,
    payload: dict[str, Any],
) -> None:
    pc = await get_pc_by_name(session, pc_name)
    if pc is None:
        logger.warning("Event from unknown PC: %s", pc_name)
        return

    event_type = str(payload.get("event", "unknown"))

    session.add(
        PcEvent(
            pc_id=pc.id,
            event_type=event_type,
            payload=payload,
        )
    )

    if event_type == "locked":
        pc.is_locked = True
        pc.lock_reason = str(payload.get("reason", pc.lock_reason))
    elif event_type == "unlocked":
        pc.is_locked = False
        pc.lock_reason = None


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Безопасный int из payload с ограничением диапазона."""
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


async def handle_server_command(
    session: AsyncSession,
    transport: AgentTransport,
    settings: Settings,
    payload: dict[str, Any],
) -> None:
    """Выполняет команды внешнего управления из топика server/cmd.

    Формат:
        {"action": "lock", "pc": "evgeny_pc", "reason": "manual", "delay_seconds": 0}
        {"action": "unlock", "pc": "evgeny_pc"}
        {"action": "add_time", "pc": "evgeny_pc", "minutes": 30}
    """
    action = str(payload.get("action", ""))
    pc_name = str(payload.get("pc", ""))

    pc = await get_pc_by_name(session, pc_name) if pc_name else None
    if pc is None:
        logger.warning("Server command for unknown PC: %r (payload=%s)", pc_name, payload)
        return

    if action == "lock":
        await lock_pc(
            session,
            transport,
            settings,
            pc,
            reason=str(payload.get("reason", "manual")),
            delay_seconds=_clamp_int(payload.get("delay_seconds"), 0, 0, 3600),
        )
    elif action == "unlock":
        await unlock_pc(session, transport, settings, pc, reason="manual")
    elif action == "add_time":
        minutes = _clamp_int(payload.get("minutes"), 30, 1, 600)
        await add_time(session, transport, settings, pc, minutes=minutes)
    else:
        logger.warning("Unknown server command action: %r", action)
        return

    logger.info("Server command executed: %s", payload)


async def build_server_status(session: AsyncSession, settings: Settings) -> dict:
    """Retained-статус сервера с актуальной сводкой по всем ПК."""
    result = await session.scalars(select(PC).where(PC.is_active.is_(True)).order_by(PC.id))
    pcs = result.all()

    local_now = datetime.now(UTC).astimezone(ZoneInfo(settings.server_timezone))
    weekday = WEEKDAY_NAMES[local_now.weekday()]

    status: dict[str, Any] = {
        "online": True,
        "ts": datetime.now(UTC).isoformat(),
        "pcs": {},
    }
    for pc in pcs:
        usage = await get_usage_today(session, settings, pc.id)
        bonus_seconds = usage.bonus_seconds if usage else 0
        limit_minutes = get_limit_minutes_for_day(pc, weekday)

        status["pcs"][pc.name] = {
            "display_name": pc.display_name,
            "is_online": pc.is_online,
            "is_locked": pc.is_locked,
            "lock_reason": pc.lock_reason,
            "desired_locked": pc.desired_locked,
            "desired_lock_reason": pc.desired_lock_reason,
            "manual_lock_until": pc.manual_lock_until.isoformat() if pc.manual_lock_until else None,
            "last_seen_at": pc.last_seen_at.isoformat() if pc.last_seen_at else None,
            "last_active_user": pc.last_active_user,
            "active_seconds": usage.active_seconds if usage else 0,
            "locked_seconds": usage.locked_seconds if usage else 0,
            "bonus_seconds": bonus_seconds,
            "is_idle": pc.is_idle,
            "idle_seconds": usage.idle_seconds if usage else 0,
            # лимит на сегодня с учётом дня недели и бонуса
            "limit_seconds": limit_minutes * 60 + bonus_seconds,
        }
    return status


async def is_allowed_now(
    session: AsyncSession,
    settings: Settings,
    pc_id: int,
    local_now: datetime,
) -> bool:
    """
    Если активных слотов расписания нет, считаем, что время разрешено.
    Позже можно сделать настройку default_deny.
    """
    weekday = WEEKDAY_NAMES[local_now.weekday()]
    current_time = local_now.time().replace(second=0, microsecond=0)

    result = await session.scalars(
        select(ScheduleSlot).where(
            ScheduleSlot.pc_id == pc_id,
            ScheduleSlot.is_active.is_(True),
        )
    )
    slots = result.all()

    if not slots:
        return True

    for slot in slots:
        days = slot.days or []
        if weekday in days and slot.allowed_from <= current_time <= slot.allowed_until:
            return True

    return False


async def _send_command(
    session: AsyncSession,
    transport: AgentTransport,
    pc: PC,
    command: dict[str, Any],
) -> None:
    await transport.send_pc_command(pc.name, command)

    session.add(
        CommandLog(
            pc_id=pc.id,
            action=str(command.get("action", "unknown")),
            payload=command,
        )
    )

    pc.last_command_at = datetime.now(UTC)


async def lock_pc(
    session: AsyncSession,
    transport: AgentTransport,
    settings: Settings,
    pc: PC,
    reason: str = "manual",
    delay_seconds: int = 0,
) -> None:
    info = await build_lock_info(session, settings, pc, reason)
    if delay_seconds > 0:
        command = {
            "action": "lock_in",
            "delay_seconds": delay_seconds,
            "reason": reason,
            "info": info,
        }
    else:
        command = {
            "action": "lock_now",
            "reason": reason,
            "info": info,
        }

    await _send_command(session, transport, pc, command)

    pc.desired_locked = True
    pc.desired_lock_reason = reason
    if reason == "manual":
        pc.manual_lock_until = get_end_of_day(settings)
    else:
        pc.manual_lock_until = None


async def unlock_pc(
    session: AsyncSession,
    transport: AgentTransport,
    settings: Settings,
    pc: PC,
    reason: str = "manual",
) -> None:
    command = {
        "action": "unlock",
        "reason": reason,
    }

    await _send_command(session, transport, pc, command)

    pc.desired_locked = False
    pc.desired_lock_reason = None
    pc.manual_lock_until = None


async def add_time(
    session: AsyncSession,
    transport: AgentTransport,
    settings: Settings,
    pc: PC,
    minutes: int,
) -> None:
    """Добавляет бонусное время на сегодня, не трогая честную историю."""
    command = {
        "action": "add_time",
        "minutes": minutes,
    }
    await _send_command(session, transport, pc, command)

    usage = await get_or_create_usage_today(session, settings, pc.id)
    usage.bonus_seconds += minutes * 60

    # Разблокируем ТОЛЬКО если блокировка была по лимиту
    # и с бонусом лимит больше не превышен.
    if pc.desired_locked and pc.desired_lock_reason == "daily_limit":
        weekday = WEEKDAY_NAMES[datetime.now(UTC).astimezone(ZoneInfo(settings.server_timezone)).weekday()]
        limit_seconds = get_limit_minutes_for_day(pc, weekday) * 60 + usage.bonus_seconds
        if usage.active_seconds < limit_seconds:
            await unlock_pc(session, transport, settings, pc, reason="add_time")


async def get_pc_with_usage_today(
    session: AsyncSession,
    settings: Settings,
    pc: PC,
) -> dict:
    """Возвращает данные ПК + использование за сегодня (для дашборда)."""
    usage = await get_usage_today(session, settings, pc.id)
    active_seconds = usage.active_seconds if usage else 0
    locked_seconds = usage.locked_seconds if usage else 0
    bonus_seconds = usage.bonus_seconds if usage else 0
    idle_seconds = usage.idle_seconds if usage else 0

    weekday = WEEKDAY_NAMES[datetime.now(UTC).astimezone(ZoneInfo(settings.server_timezone)).weekday()]
    limit_seconds = get_limit_minutes_for_day(pc, weekday) * 60 + bonus_seconds

    usage_percent = 0.0
    if limit_seconds > 0:
        usage_percent = round((active_seconds / limit_seconds) * 100, 1)

    return {
        "pc": pc,
        "usage_today": {
            "active_seconds": active_seconds,
            "locked_seconds": locked_seconds,
            "bonus_seconds": bonus_seconds,
            "idle_seconds": idle_seconds,
            "limit_seconds": limit_seconds,
            "usage_percent": min(usage_percent, 100.0),
        },
    }


async def get_usage_period(
    session: AsyncSession,
    settings: Settings,
    pc_id: int,
    days: int = 7,
) -> list[UsageDaily]:
    """Использование за последние N дней."""
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.server_timezone)).date()
    since_date = today - timedelta(days=days - 1)

    result = await session.scalars(
        select(UsageDaily)
        .where(
            UsageDaily.pc_id == pc_id,
            UsageDaily.usage_date >= since_date,
        )
        .order_by(UsageDaily.usage_date)
    )
    return list(result.all())


async def get_pc_events(
    session: AsyncSession,
    pc_id: int,
    limit: int = 50,
) -> list[PcEvent]:
    """Последние события от агента."""
    result = await session.scalars(
        select(PcEvent).where(PcEvent.pc_id == pc_id).order_by(PcEvent.created_at.desc()).limit(limit)
    )
    return list(result.all())


async def get_pc_commands(
    session: AsyncSession,
    pc_id: int,
    limit: int = 50,
) -> list[CommandLog]:
    """Последние команды, отправленные на ПК."""
    result = await session.scalars(
        select(CommandLog).where(CommandLog.pc_id == pc_id).order_by(CommandLog.created_at.desc()).limit(limit)
    )
    return list(result.all())


async def get_pc_schedule(
    session: AsyncSession,
    pc_id: int,
) -> list[ScheduleSlot]:
    """Текущее расписание ПК."""
    result = await session.scalars(select(ScheduleSlot).where(ScheduleSlot.pc_id == pc_id).order_by(ScheduleSlot.id))
    return list(result.all())


async def update_pc_schedule(
    session: AsyncSession,
    pc_id: int,
    slots: list[dict],
) -> list[ScheduleSlot]:
    """Полностью заменяет расписание ПК."""
    # Удаляем старые слоты
    old_slots = await get_pc_schedule(session, pc_id)
    for slot in old_slots:
        await session.delete(slot)
    await session.flush()

    # Создаём новые
    new_slots = []
    for slot_data in slots:
        slot = ScheduleSlot(
            pc_id=pc_id,
            days=slot_data["days"],
            allowed_from=slot_data["allowed_from"],
            allowed_until=slot_data["allowed_until"],
            is_active=slot_data.get("is_active", True),
        )
        session.add(slot)
        new_slots.append(slot)

    await session.flush()
    return new_slots


async def get_stats(session: AsyncSession, settings: Settings) -> dict:
    """Общая статистика по всем ПК."""
    result = await session.scalars(select(PC).where(PC.is_active.is_(True)))
    pcs = result.all()

    total_active_seconds = 0
    for pc in pcs:
        usage = await get_usage_today(session, settings, pc.id)
        if usage:
            total_active_seconds += usage.active_seconds

    return {
        "total_pcs": len(pcs),
        "online_pcs": sum(1 for pc in pcs if pc.is_online),
        "locked_pcs": sum(1 for pc in pcs if pc.is_locked),
        "total_active_seconds_today": total_active_seconds,
    }


LOCK_REASON_RU = {
    "daily_limit": "Время за компьютером на сегодня закончилось",
    "schedule": "Сейчас по расписанию — перерыв",
    "manual": "Компьютер заблокирован родителями",
}


async def _next_allowed_window(
    session: AsyncSession,
    settings: Settings,
    pc: PC,
    local_now: datetime,
    skip_today: bool = False,
) -> dict | None:
    """Ближайшее будущее разрешённое окно расписания."""
    slots = [s for s in await get_pc_schedule(session, pc.id) if s.is_active]
    if not slots:
        return None
    tz = ZoneInfo(settings.server_timezone)
    for offset in range(1 if skip_today else 0, 8):
        day = local_now.date() + timedelta(days=offset)
        weekday = WEEKDAY_NAMES[day.weekday()]
        for slot in sorted(slots, key=lambda s: s.allowed_from):
            if weekday not in (slot.days or []):
                continue
            start = datetime.combine(day, slot.allowed_from, tzinfo=tz)
            if offset == 0 and local_now >= start:
                continue  # окно уже началось сегодня
            return {
                "label": f"{slot.allowed_from.strftime('%H:%M')}–{slot.allowed_until.strftime('%H:%M')}",
                "start_iso": start.isoformat(),
            }
    return None


async def build_lock_info(
    session: AsyncSession,
    settings: Settings,
    pc: PC,
    reason: str,
) -> dict[str, Any]:
    """Данные для экрана блокировки агента: сервер считает, агент рисует."""
    local_now = datetime.now(UTC).astimezone(ZoneInfo(settings.server_timezone))
    info: dict[str, Any] = {
        "display_name": pc.display_name,
        "reason": reason,
        "reason_ru": LOCK_REASON_RU.get(reason, "Компьютер заблокирован"),
        "locked_at": local_now.isoformat(),
        "unlocks_at": None,
        "next_window": None,
    }
    if reason == "manual":
        info["unlocks_at"] = (pc.manual_lock_until or get_end_of_day(settings)).isoformat()
        info["next_window"] = "до конца дня"
        return info

    window = await _next_allowed_window(session, settings, pc, local_now, skip_today=(reason == "daily_limit"))
    if window is not None:
        info["next_window"] = window["label"]
        info["unlocks_at"] = window["start_iso"]
    elif reason == "daily_limit":
        info["next_window"] = "завтра"
    return info
