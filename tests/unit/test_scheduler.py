"""Тесты для логики планировщика."""

from datetime import datetime, time, timezone

import pytest
from zoneinfo import ZoneInfo

from app.core.scheduler import process_pc
from app.models import PC, ScheduleSlot, UsageDaily


def make_now():
    """Фиксированное время: 2026-08-21 14:00 Yekaterinburg (09:00 UTC)."""
    now_utc = datetime(2026, 8, 21, 9, 0, 0, tzinfo=timezone.utc)  # 09:00 UTC = 14:00 Yekaterinburg
    local_now = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    return now_utc, local_now


async def create_online_pc(session, name="sched_pc", daily_limit=120):
    now_utc, _ = make_now()
    pc = PC(
        name=name,
        display_name=name,
        daily_limit_minutes=daily_limit,
        is_online=True,
        last_seen_at=now_utc,
    )
    session.add(pc)
    await session.commit()
    return pc


async def add_restrictive_slot(session, pc_id):
    """Слот 22:00–23:00 — не покрывает 14:00, значит сейчас запрещено."""
    slot = ScheduleSlot(
        pc_id=pc_id,
        days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        allowed_from=time(22, 0),
        allowed_until=time(23, 0),
        is_active=True,
    )
    session.add(slot)
    await session.commit()


class TestProcessPc:
    @pytest.mark.asyncio()
    async def test_lock_by_schedule(self, settings, db_session, mocker):
        """Вне разрешённого окна планировщик блокирует по расписанию."""
        pc = await create_online_pc(db_session)
        await add_restrictive_slot(db_session, pc.id)
        mqtt_mock = mocker.AsyncMock()
        now_utc, local_now = make_now()

        await process_pc(db_session, settings, mqtt_mock, pc, now_utc, local_now)

        assert pc.desired_locked is True
        assert pc.desired_lock_reason == "schedule"
        mqtt_mock.send_pc_command.assert_called_once()
        cmd = mqtt_mock.send_pc_command.call_args[0][1]
        assert cmd["action"] == "lock_in"  # warning_before_lock_seconds > 0

    @pytest.mark.asyncio()
    async def test_lock_by_daily_limit(self, settings, db_session, mocker):
        """При превышении дневного лимита блокирует, даже если расписание разрешает."""
        pc = await create_online_pc(db_session, daily_limit=1)  # 60 сек
        # Создаём usage с превышением
        now_utc, local_now = make_now()
        usage = UsageDaily(
            pc_id=pc.id,
            usage_date=local_now.date(),
            active_seconds=120,
            locked_seconds=0,
        )
        db_session.add(usage)
        await db_session.commit()
        mqtt_mock = mocker.AsyncMock()

        await process_pc(db_session, settings, mqtt_mock, pc, now_utc, local_now)

        assert pc.desired_locked is True
        assert pc.desired_lock_reason == "daily_limit"

    @pytest.mark.asyncio()
    async def test_manual_lock_not_released_by_scheduler(self, settings, db_session, mocker):
        """Ручную блокировку планировщик НЕ снимает, даже если время разрешено."""
        pc = await create_online_pc(db_session)
        pc.desired_locked = True
        pc.desired_lock_reason = "manual"
        now_utc, local_now = make_now()
        # manual_lock_until в будущем
        pc.manual_lock_until = now_utc + timezone.utc.utcoffset(None) or now_utc
        from datetime import timedelta

        pc.manual_lock_until = now_utc + timedelta(hours=5)
        await db_session.commit()
        mqtt_mock = mocker.AsyncMock()

        await process_pc(db_session, settings, mqtt_mock, pc, now_utc, local_now)

        # Расписание разрешает (нет слотов) → should_lock=False,
        # но reason=manual → unlock НЕ вызывается
        assert pc.desired_locked is True
        mqtt_mock.send_pc_command.assert_not_called()

    @pytest.mark.asyncio()
    async def test_schedule_lock_released(self, settings, db_session, mocker):
        """Блокировку по расписанию планировщик снимает, когда время разрешено."""
        pc = await create_online_pc(db_session)
        pc.desired_locked = True
        pc.desired_lock_reason = "schedule"
        await db_session.commit()
        mqtt_mock = mocker.AsyncMock()
        now_utc, local_now = make_now()

        await process_pc(db_session, settings, mqtt_mock, pc, now_utc, local_now)

        assert pc.desired_locked is False
        cmd = mqtt_mock.send_pc_command.call_args[0][1]
        assert cmd["action"] == "unlock"

    @pytest.mark.asyncio()
    async def test_offline_pc_skipped(self, settings, db_session, mocker):
        """Оффлайн ПК планировщик не трогает."""
        pc = await create_online_pc(db_session)
        pc.is_online = False
        await db_session.commit()
        mqtt_mock = mocker.AsyncMock()
        now_utc, local_now = make_now()

        await process_pc(db_session, settings, mqtt_mock, pc, now_utc, local_now)

        mqtt_mock.send_pc_command.assert_not_called()

    @pytest.mark.asyncio()
    async def test_heartbeat_timeout_marks_offline(self, settings, db_session, mocker):
        """Если heartbeat давно не приходил, ПК помечается оффлайн."""
        now_utc, local_now = make_now()
        pc = await create_online_pc(db_session)
        # last_seen_at = 200 секунд назад (timeout 90)
        from datetime import timedelta

        pc.last_seen_at = now_utc - timedelta(seconds=200)
        await db_session.commit()
        mqtt_mock = mocker.AsyncMock()

        await process_pc(db_session, settings, mqtt_mock, pc, now_utc, local_now)

        assert pc.is_online is False
        mqtt_mock.send_pc_command.assert_not_called()
