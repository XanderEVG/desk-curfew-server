from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models import PC, CommandLog, PcEvent, ScheduleSlot, UsageDaily

if TYPE_CHECKING:
    from app.core.mqtt_client import MqttService

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


async def handle_heartbeat(
    session: AsyncSession,
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

    usage = await get_or_create_usage_today(session, settings, pc.id)

    if locked:
        usage.locked_seconds += settings.heartbeat_interval
    else:
        usage.active_seconds += settings.heartbeat_interval


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


async def handle_server_command(
    session: AsyncSession,
    settings: Settings,
    payload: dict[str, Any],
) -> None:
    """
    Сюда будут приходить команды вида:
    {"action": "lock", "pc": "evgeny_pc"}
    {"action": "set_config", ...}
    """
    logger.info("Server command received: %s", payload)


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
    mqtt_service: MqttService,
    pc: PC,
    command: dict[str, Any],
) -> None:
    await mqtt_service.send_pc_command(pc.name, command)

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
    mqtt_service: MqttService,
    settings: Settings,
    pc: PC,
    reason: str = "manual",
    delay_seconds: int = 0,
) -> None:
    if delay_seconds > 0:
        command = {
            "action": "lock_in",
            "delay_seconds": delay_seconds,
            "reason": reason,
        }
    else:
        command = {
            "action": "lock_now",
            "reason": reason,
        }

    await _send_command(session, mqtt_service, pc, command)

    pc.desired_locked = True
    pc.desired_lock_reason = reason
    if reason == "manual":
        pc.manual_lock_until = get_end_of_day(settings)
    else:
        pc.manual_lock_until = None


async def unlock_pc(
    session: AsyncSession,
    mqtt_service: MqttService,
    settings: Settings,
    pc: PC,
    reason: str = "manual",
) -> None:
    command = {
        "action": "unlock",
        "reason": reason,
    }

    await _send_command(session, mqtt_service, pc, command)

    pc.desired_locked = False
    pc.desired_lock_reason = None
    pc.manual_lock_until = None


async def add_time(
    session: AsyncSession,
    mqtt_service: MqttService,
    settings: Settings,
    pc: PC,
    minutes: int,
) -> None:
    command = {
        "action": "add_time",
        "minutes": minutes,
    }

    await _send_command(session, mqtt_service, pc, command)

    usage = await get_or_create_usage_today(session, settings, pc.id)
    usage.active_seconds = max(0, usage.active_seconds - minutes * 60)

    if pc.desired_locked:
        await unlock_pc(session, mqtt_service, settings, pc, reason="add_time")


async def get_pc_with_usage_today(
    session: AsyncSession,
    settings: Settings,
    pc: PC,
) -> dict:
    """Возвращает данные ПК + использование за сегодня (для дашборда)."""
    usage = await get_usage_today(session, settings, pc.id)

    active_seconds = usage.active_seconds if usage else 0
    locked_seconds = usage.locked_seconds if usage else 0
    limit_seconds = pc.daily_limit_minutes * 60

    usage_percent = 0.0
    if limit_seconds > 0:
        usage_percent = round((active_seconds / limit_seconds) * 100, 1)

    return {
        "pc": pc,
        "usage_today": {
            "active_seconds": active_seconds,
            "locked_seconds": locked_seconds,
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
