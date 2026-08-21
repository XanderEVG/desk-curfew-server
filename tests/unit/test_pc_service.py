"""Тесты для бизнес-логики управления ПК."""

from datetime import datetime, time, timezone

import pytest
from freezegun import freeze_time
from zoneinfo import ZoneInfo

from app.core import pc_service
from app.core.pc_service import get_end_of_day, is_allowed_now


class TestGetEndOfDay:
    """Тесты для функции get_end_of_day."""

    @freeze_time("2026-08-21 14:30:00", tz_offset=5)  # Asia/Yekaterinburg = UTC+5
    def test_end_of_day_same_day(self, settings):
        """Конец дня должен быть в 00:00 следующего дня в локальной таймзоне."""
        now_utc = datetime(2026, 8, 21, 9, 30, 0)  # 14:30 в Yekaterinburg (UTC+5)
        end = get_end_of_day(settings, now_utc)

        # Конец дня в Yekaterinburg = 00:00 22 августа = 19:00 UTC 21 августа
        expected = datetime(2026, 8, 21, 19, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert end == expected

    @freeze_time("2026-08-21 23:59:00", tz_offset=5)
    def test_end_of_day_near_midnight(self, settings):
        """Даже в 23:59 конец дня должен быть в 00:00 следующего дня."""
        now_utc = datetime(2026, 8, 21, 18, 59, 0)  # 23:59 в Yekaterinburg (UTC+5)
        end = get_end_of_day(settings, now_utc)

        expected = datetime(2026, 8, 21, 19, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert end == expected


class TestIsAllowedNow:
    """Тесты для проверки расписания."""

    @pytest.mark.asyncio()
    async def test_no_schedule_allows_all(self, settings, db_session):
        """Если расписания нет, должно быть разрешено всегда."""
        pc_id = 1
        local_now = datetime(2026, 8, 21, 14, 0, 0)  # Пятница 14:00

        allowed = await is_allowed_now(db_session, settings, pc_id, local_now)
        assert allowed is True

    @pytest.mark.asyncio()
    async def test_within_schedule(self, settings, db_session):
        """Время внутри разрешённого окна должно быть разрешено."""
        pc_id = await create_test_pc_with_schedule(
            db_session,
            days=["mon", "tue", "wed", "thu", "fri"],
            allowed_from=time(16, 0),
            allowed_until=time(20, 0),
        )

        # Пятница 18:30 — внутри окна
        local_now = datetime(2026, 8, 21, 18, 30, 0)
        allowed = await is_allowed_now(db_session, settings, pc_id, local_now)
        assert allowed is True

    @pytest.mark.asyncio()
    async def test_outside_schedule(self, settings, db_session):
        """Время вне разрешённого окна должно быть запрещено."""
        pc_id = await create_test_pc_with_schedule(
            db_session,
            days=["mon", "tue", "wed", "thu", "fri"],
            allowed_from=time(16, 0),
            allowed_until=time(20, 0),
        )

        # Пятница 21:30 — вне окна
        local_now = datetime(2026, 8, 21, 21, 30, 0)
        allowed = await is_allowed_now(db_session, settings, pc_id, local_now)
        assert allowed is False

    @pytest.mark.asyncio()
    async def test_wrong_day(self, settings, db_session):
        """Время в правильном диапазоне, но не тот день недели."""
        pc_id = await create_test_pc_with_schedule(
            db_session,
            days=["mon", "tue", "wed", "thu", "fri"],
            allowed_from=time(16, 0),
            allowed_until=time(20, 0),
        )

        # Суббота 18:30 — правильное время, но не тот день
        local_now = datetime(2026, 8, 22, 18, 30, 0)  # Суббота
        allowed = await is_allowed_now(db_session, settings, pc_id, local_now)
        assert allowed is False

    @pytest.mark.asyncio()
    async def test_multiple_slots(self, settings, db_session):
        """Несколько слотов расписания должны работать."""
        pc_id = await create_test_pc_with_multiple_slots(
            db_session,
            slots=[
                (["mon", "tue", "wed", "thu", "fri"], time(16, 0), time(20, 0)),
                (["sat", "sun"], time(10, 0), time(21, 0)),
            ],
        )

        # Суббота 15:00 — попадает во второй слот
        local_now = datetime(2026, 8, 22, 15, 0, 0)
        allowed = await is_allowed_now(db_session, settings, pc_id, local_now)
        assert allowed is True

    @pytest.mark.asyncio()
    async def test_inactive_slot_ignored(self, settings, db_session):
        """Неактивные слоты должны игнорироваться."""
        pc_id = await create_test_pc_with_schedule(
            db_session,
            days=["mon", "tue", "wed", "thu", "fri"],
            allowed_from=time(16, 0),
            allowed_until=time(20, 0),
            is_active=False,  # Слот неактивен
        )

        local_now = datetime(2026, 8, 21, 18, 30, 0)
        allowed = await is_allowed_now(db_session, settings, pc_id, local_now)
        assert allowed is True  # Нет активных слотов → разрешено


# Вспомогательные функции для создания тестовых данных
async def create_test_pc_with_schedule(
    session,
    days: list[str],
    allowed_from: time,
    allowed_until: time,
    is_active: bool = True,
) -> int:
    """Создаёт тестовый ПК с одним слотом расписания."""
    from app.models import PC, ScheduleSlot

    pc = PC(
        name="test_pc",
        display_name="Test PC",
        daily_limit_minutes=120,
    )
    session.add(pc)
    await session.flush()

    slot = ScheduleSlot(
        pc_id=pc.id,
        days=days,
        allowed_from=allowed_from,
        allowed_until=allowed_until,
        is_active=is_active,
    )
    session.add(slot)
    await session.commit()

    return pc.id


async def create_test_pc_with_multiple_slots(
    session,
    slots: list[tuple[list[str], time, time]],
) -> int:
    """Создаёт тестовый ПК с несколькими слотами."""
    from app.models import PC, ScheduleSlot

    pc = PC(
        name="test_pc_multi",
        display_name="Test PC Multi",
        daily_limit_minutes=120,
    )
    session.add(pc)
    await session.flush()

    for days, allowed_from, allowed_until in slots:
        slot = ScheduleSlot(
            pc_id=pc.id,
            days=days,
            allowed_from=allowed_from,
            allowed_until=allowed_until,
            is_active=True,
        )
        session.add(slot)

    await session.commit()
    return pc.id


# Вспомогательная функция для создания тестового ПК (общая для всех тестов)
async def create_test_pc(session, name="test_pc"):
    from app.models import PC

    pc = PC(name=name, display_name=name, daily_limit_minutes=120)
    session.add(pc)
    await session.commit()
    return pc


class TestHandleHeartbeat:
    """Тесты для обработки heartbeat от агентов."""

    @pytest.mark.asyncio()
    async def test_active_time_increases_when_unlocked(self, settings, db_session):
        """Когда locked=false, должен расти active_seconds."""
        pc = await create_test_pc(db_session, name="hb_test")

        payload = {"ts": "2026-08-21T10:00:00Z", "active_user": "kid1", "locked": False}
        await pc_service.handle_heartbeat(db_session, settings, "hb_test", payload)
        await db_session.commit()

        usage = await pc_service.get_usage_today(db_session, settings, pc.id)
        assert usage is not None
        assert usage.active_seconds == settings.heartbeat_interval  # 30
        assert usage.locked_seconds == 0

    @pytest.mark.asyncio()
    async def test_locked_time_increases_when_locked(self, settings, db_session):
        """Когда locked=true, должен расти locked_seconds."""
        pc = await create_test_pc(db_session, name="hb_locked_test")

        payload = {"ts": "2026-08-21T10:00:00Z", "active_user": "kid1", "locked": True}
        await pc_service.handle_heartbeat(db_session, settings, "hb_locked_test", payload)
        await db_session.commit()

        usage = await pc_service.get_usage_today(db_session, settings, pc.id)
        assert usage.active_seconds == 0
        assert usage.locked_seconds == settings.heartbeat_interval  # 30

    @pytest.mark.asyncio()
    async def test_heartbeat_updates_pc_fields(self, settings, db_session):
        """Heartbeat должен обновлять is_online, last_seen_at, last_active_user."""
        pc = await create_test_pc(db_session, name="hb_fields_test")
        assert pc.is_online is False
        assert pc.last_seen_at is None

        payload = {"ts": "2026-08-21T10:00:00Z", "active_user": "kid1", "locked": False}
        await pc_service.handle_heartbeat(db_session, settings, "hb_fields_test", payload)
        await db_session.commit()
        await db_session.refresh(pc)

        assert pc.is_online is True
        assert pc.last_seen_at is not None
        assert pc.last_active_user == "kid1"

    @pytest.mark.asyncio()
    async def test_unknown_pc_does_not_crash(self, settings, db_session, caplog):
        """Heartbeat от неизвестного ПК должен логировать warning, а не падать."""
        payload = {"ts": "2026-08-21T10:00:00Z", "locked": False}
        await pc_service.handle_heartbeat(db_session, settings, "unknown_pc", payload)

        assert any("unknown pc" in rec.message.lower() for rec in caplog.records)


class TestLockPc:
    """Тесты для формирования команд блокировки."""

    @pytest.mark.asyncio()
    async def test_lock_now_when_no_delay(self, settings, db_session, mocker):
        """Без задержки должна отправляться команда lock_now."""
        pc = await create_test_pc(db_session, name="lock_now_test")
        mqtt_mock = mocker.AsyncMock()

        await pc_service.lock_pc(db_session, mqtt_mock, settings, pc, reason="manual")

        mqtt_mock.send_pc_command.assert_called_once()
        args = mqtt_mock.send_pc_command.call_args
        assert args[0][0] == pc.name
        assert args[0][1]["action"] == "lock_now"
        assert args[0][1]["reason"] == "manual"

    @pytest.mark.asyncio()
    async def test_lock_in_when_delay_given(self, settings, db_session, mocker):
        """С задержкой должна отправляться команда lock_in."""
        pc = await create_test_pc(db_session, name="lock_in_test")
        mqtt_mock = mocker.AsyncMock()

        await pc_service.lock_pc(
            db_session,
            mqtt_mock,
            settings,
            pc,
            reason="schedule",
            delay_seconds=300,
        )

        args = mqtt_mock.send_pc_command.call_args[0]
        assert args[1]["action"] == "lock_in"
        assert args[1]["delay_seconds"] == 300
        assert args[1]["reason"] == "schedule"

    @pytest.mark.asyncio()
    async def test_manual_lock_sets_manual_lock_until(self, settings, db_session, mocker):
        """При ручной блокировке должно установиться manual_lock_until (конец дня)."""
        pc = await create_test_pc(db_session, name="manual_lock_test")
        mqtt_mock = mocker.AsyncMock()

        await pc_service.lock_pc(
            db_session,
            mqtt_mock,
            settings,
            pc,
            reason="manual",
            delay_seconds=0,
        )

        assert pc.desired_locked is True
        assert pc.desired_lock_reason == "manual"
        assert pc.manual_lock_until is not None
        # Конец дня должен быть в будущем
        assert pc.manual_lock_until > datetime.now(timezone.utc)

    @pytest.mark.asyncio()
    async def test_schedule_lock_does_not_set_manual_until(self, settings, db_session, mocker):
        """Для блокировки по расписанию manual_lock_until должно быть None."""
        pc = await create_test_pc(db_session, name="schedule_lock_test")
        mqtt_mock = mocker.AsyncMock()

        await pc_service.lock_pc(
            db_session,
            mqtt_mock,
            settings,
            pc,
            reason="schedule",
            delay_seconds=0,
        )

        assert pc.desired_locked is True
        assert pc.desired_lock_reason == "schedule"
        assert pc.manual_lock_until is None


class TestUnlockPc:
    """Тесты для разблокировки."""

    @pytest.mark.asyncio()
    async def test_unlock_clears_all_fields(self, settings, db_session, mocker):
        """Разблокировка должна сбросить desired_locked, reason и manual_lock_until."""
        pc = await create_test_pc(db_session, name="unlock_test")
        mqtt_mock = mocker.AsyncMock()

        # Сначала заблокируем вручную
        await pc_service.lock_pc(
            db_session,
            mqtt_mock,
            settings,
            pc,
            reason="manual",
        )
        assert pc.desired_locked is True
        assert pc.manual_lock_until is not None

        # Теперь разблокируем
        await pc_service.unlock_pc(db_session, mqtt_mock, settings, pc)

        assert pc.desired_locked is False
        assert pc.desired_lock_reason is None
        assert pc.manual_lock_until is None

        # И была отправлена команда unlock
        args = mqtt_mock.send_pc_command.call_args_list[-1][0]
        assert args[1]["action"] == "unlock"
