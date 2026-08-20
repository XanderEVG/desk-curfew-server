from __future__ import annotations

import logging
from datetime import UTC, datetime
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
