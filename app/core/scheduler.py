from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from zoneinfo import ZoneInfo

from app.config import Settings
from app.core import pc_service
from app.core.mqtt_client import MqttService
from app.database import AsyncSessionLocal
from app.models import PC

logger = logging.getLogger(__name__)


async def run_scheduler(settings: Settings, mqtt_service: MqttService) -> None:
    await asyncio.sleep(5)

    while True:
        try:
            await tick(settings, mqtt_service)
        except Exception:
            logger.exception("Scheduler tick failed")

        await asyncio.sleep(30)


async def tick(settings: Settings, mqtt_service: MqttService) -> None:
    async with AsyncSessionLocal() as session:
        await tick_with_session(session, settings, mqtt_service)


async def tick_with_session(session, settings: Settings, mqtt_service: MqttService) -> None:
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone(ZoneInfo(settings.server_timezone))

    result = await session.scalars(select(PC).where(PC.is_active.is_(True)))
    pcs = result.all()

    for pc in pcs:
        await process_pc(session, settings, mqtt_service, pc, now_utc, local_now)

    await session.commit()


async def process_pc(
    session,
    settings: Settings,
    mqtt_service: MqttService,
    pc: PC,
    now_utc: datetime,
    local_now: datetime,
) -> None:
    """Обрабатывает один ПК: проверяет оффлайн, лимиты, расписание, блокировки."""
    # Проверка оффлайн по heartbeat
    if (
        pc.last_seen_at is not None
        and now_utc - pc.last_seen_at > timedelta(seconds=settings.heartbeat_timeout)
        and pc.is_online
    ):
        logger.info("PC %s is now offline by heartbeat timeout", pc.name)
        pc.is_online = False

    if not pc.is_online:
        return

    # Истечение ручной блокировки
    if (
        pc.desired_locked
        and pc.desired_lock_reason == "manual"
        and pc.manual_lock_until is not None
        and now_utc >= pc.manual_lock_until
    ):
        logger.info("Manual lock expired for PC %s, unlocking", pc.name)
        await pc_service.unlock_pc(session, mqtt_service, settings, pc, reason="manual_lock_expired")

    usage = await pc_service.get_usage_today(session, settings, pc.id)
    active_seconds = usage.active_seconds if usage else 0

    over_limit = pc.daily_limit_minutes > 0 and active_seconds >= pc.daily_limit_minutes * 60

    allowed = await pc_service.is_allowed_now(session, settings, pc.id, local_now)

    should_lock = (not allowed) or over_limit
    reason = "daily_limit" if over_limit else "schedule"

    if should_lock and not pc.desired_locked:
        logger.info("Locking PC %s by reason %s", pc.name, reason)
        await pc_service.lock_pc(
            session,
            mqtt_service,
            settings,
            pc,
            reason=reason,
            delay_seconds=pc.warning_before_lock_seconds,
        )

    elif not should_lock and pc.desired_locked:
        if pc.desired_lock_reason in {"schedule", "daily_limit"}:
            logger.info(
                "Unlocking PC %s because lock reason %s is no longer active",
                pc.name,
                pc.desired_lock_reason,
            )
            await pc_service.unlock_pc(session, mqtt_service, settings, pc, reason="scheduler")

    elif pc.desired_locked and not pc.is_locked:
        if pc.last_command_at and now_utc - pc.last_command_at > timedelta(seconds=120):
            logger.warning(
                "PC %s desired locked but agent is not locked. Resending lock_now.",
                pc.name,
            )
            await pc_service.lock_pc(
                session,
                mqtt_service,
                settings,
                pc,
                reason=pc.desired_lock_reason or "schedule",
                delay_seconds=0,
            )
