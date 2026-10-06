"""Тесты для бизнес-логики управления ПК."""

from datetime import datetime, time, timedelta, timezone

import pytest
from freezegun import freeze_time
from zoneinfo import ZoneInfo

from app.core import pc_service
from app.core.pc_service import get_end_of_day, is_allowed_now
from app.models import PC, ScheduleSlot, UsageDaily


def _today(settings):
    """Реальная сегодняшняя дата в таймзоне сервера."""
    return datetime.now(timezone.utc).astimezone(ZoneInfo(settings.server_timezone)).date()


async def create_test_pc(session, name="test_pc"):
    """Создаёт тестовый ПК."""
    pc = PC(name=name, display_name=name, daily_limit_minutes=120)
    session.add(pc)
    await session.commit()
    return pc


async def create_test_pc_with_schedule(
    session,
    days: list[str],
    allowed_from: time,
    allowed_until: time,
    is_active: bool = True,
) -> int:
    """Создаёт тестовый ПК с одним слотом расписания."""
    pc = PC(name="test_pc", display_name="Test PC", daily_limit_minutes=120)
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
    pc = PC(name="test_pc_multi", display_name="Test PC Multi", daily_limit_minutes=120)
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


class TestHandleHeartbeat:
    """Тесты для обработки heartbeat от агентов."""

    @pytest.mark.asyncio()
    async def test_active_time_increases_when_unlocked(self, settings, db_session, mocker):
        """Когда locked=false и есть ввод, должен расти active_seconds."""
        pc = await create_test_pc(db_session, name="hb_test")
        transport_mock = mocker.AsyncMock()
        payload = {"ts": "2026-08-21T10:00:00Z", "active_user": "kid1", "locked": False}

        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "hb_test", payload)
        await db_session.commit()

        usage = await pc_service.get_usage_today(db_session, settings, pc.id)
        assert usage is not None
        assert usage.active_seconds == settings.heartbeat_interval  # 30
        assert usage.locked_seconds == 0

    @pytest.mark.asyncio()
    async def test_locked_time_increases_when_locked(self, settings, db_session, mocker):
        """Когда locked=true, должен расти locked_seconds."""
        pc = await create_test_pc(db_session, name="hb_locked_test")
        transport_mock = mocker.AsyncMock()
        payload = {"ts": "2026-08-21T10:00:00Z", "active_user": "kid1", "locked": True}

        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "hb_locked_test", payload)
        await db_session.commit()

        usage = await pc_service.get_usage_today(db_session, settings, pc.id)
        assert usage.active_seconds == 0
        assert usage.locked_seconds == settings.heartbeat_interval  # 30

    @pytest.mark.asyncio()
    async def test_idle_time_goes_to_idle_seconds(self, settings, db_session, mocker):
        """Без ввода время капает в idle_seconds, лимит не тратится."""
        pc = await create_test_pc(db_session, name="hb_idle_pc")
        transport_mock = mocker.AsyncMock()
        payload = {"ts": "2026-08-21T10:00:00Z", "locked": False, "idle_seconds": 300}

        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "hb_idle_pc", payload)
        await db_session.commit()

        usage = await pc_service.get_usage_today(db_session, settings, pc.id)
        assert usage.idle_seconds == settings.heartbeat_interval
        assert usage.active_seconds == 0
        assert pc.is_idle is True

    @pytest.mark.asyncio()
    async def test_heartbeat_updates_pc_fields(self, settings, db_session, mocker):
        """Heartbeat должен обновлять is_online, last_seen_at, last_active_user."""
        pc = await create_test_pc(db_session, name="hb_fields_test")
        transport_mock = mocker.AsyncMock()
        assert pc.is_online is False
        assert pc.last_seen_at is None

        payload = {"ts": "2026-08-21T10:00:00Z", "active_user": "kid1", "locked": False}
        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "hb_fields_test", payload)
        await db_session.commit()
        await db_session.refresh(pc)

        assert pc.is_online is True
        assert pc.last_seen_at is not None
        assert pc.last_active_user == "kid1"

    @pytest.mark.asyncio()
    async def test_heartbeat_resends_lock_when_desired(self, settings, db_session, mocker):
        """Агент жив, но не заблокирован при desired_locked — немедленный resend."""
        pc = await create_test_pc(db_session, name="hb_resend_pc")
        pc.desired_locked = True
        pc.desired_lock_reason = "schedule"
        pc.last_command_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "hb_resend_pc", {"locked": False})
        await db_session.commit()

        assert transport_mock.send_pc_command.call_count == 1
        assert transport_mock.send_pc_command.call_args[0][1]["action"] == "lock_now"

    @pytest.mark.asyncio()
    async def test_no_resend_if_command_recent(self, settings, db_session, mocker):
        """Недавняя команда (< 120 с) — resend не шлётся."""
        pc = await create_test_pc(db_session, name="hb_no_resend_pc")
        pc.desired_locked = True
        pc.desired_lock_reason = "schedule"
        pc.last_command_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "hb_no_resend_pc", {"locked": False})
        await db_session.commit()

        transport_mock.send_pc_command.assert_not_called()

    @pytest.mark.asyncio()
    async def test_unknown_pc_does_not_crash(self, settings, db_session, mocker, caplog):
        """Heartbeat от неизвестного ПК должен логировать warning, а не падать."""
        transport_mock = mocker.AsyncMock()
        payload = {"ts": "2026-08-21T10:00:00Z", "locked": False}

        await pc_service.handle_heartbeat(db_session, transport_mock, settings, "unknown_pc", payload)

        assert any("unknown pc" in rec.message.lower() for rec in caplog.records)
        transport_mock.send_pc_command.assert_not_called()


class TestLockPc:
    """Тесты для формирования команд блокировки."""

    @pytest.mark.asyncio()
    async def test_lock_now_when_no_delay(self, settings, db_session, mocker):
        """Без задержки должна отправляться команда lock_now."""
        pc = await create_test_pc(db_session, name="lock_now_test")
        transport_mock = mocker.AsyncMock()

        await pc_service.lock_pc(db_session, transport_mock, settings, pc, reason="manual")

        transport_mock.send_pc_command.assert_called_once()
        args = transport_mock.send_pc_command.call_args
        assert args[0][0] == pc.name
        assert args[0][1]["action"] == "lock_now"
        assert args[0][1]["reason"] == "manual"

    @pytest.mark.asyncio()
    async def test_lock_in_when_delay_given(self, settings, db_session, mocker):
        """С задержкой должна отправляться команда lock_in."""
        pc = await create_test_pc(db_session, name="lock_in_test")
        transport_mock = mocker.AsyncMock()

        await pc_service.lock_pc(
            db_session,
            transport_mock,
            settings,
            pc,
            reason="schedule",
            delay_seconds=300,
        )

        args = transport_mock.send_pc_command.call_args[0]
        assert args[1]["action"] == "lock_in"
        assert args[1]["delay_seconds"] == 300
        assert args[1]["reason"] == "schedule"

    @pytest.mark.asyncio()
    async def test_lock_command_contains_info(self, settings, db_session, mocker):
        """Lock-команда несёт данные для экрана блокировки."""
        pc = await create_test_pc(db_session, name="info_pc")
        transport_mock = mocker.AsyncMock()

        await pc_service.lock_pc(db_session, transport_mock, settings, pc, reason="manual")

        cmd = transport_mock.send_pc_command.call_args[0][1]
        assert cmd["info"]["display_name"] == "info_pc"
        assert cmd["info"]["reason"] == "manual"
        assert cmd["info"]["reason_ru"] == "Компьютер заблокирован родителями"
        assert cmd["info"]["next_window"] == "до конца дня"
        assert cmd["info"]["unlocks_at"] is not None

    @pytest.mark.asyncio()
    async def test_manual_lock_sets_manual_lock_until(self, settings, db_session, mocker):
        """При ручной блокировке должно установиться manual_lock_until (конец дня)."""
        pc = await create_test_pc(db_session, name="manual_lock_test")
        transport_mock = mocker.AsyncMock()

        await pc_service.lock_pc(
            db_session,
            transport_mock,
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
        transport_mock = mocker.AsyncMock()

        await pc_service.lock_pc(
            db_session,
            transport_mock,
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
        transport_mock = mocker.AsyncMock()

        # Сначала заблокируем вручную
        await pc_service.lock_pc(
            db_session,
            transport_mock,
            settings,
            pc,
            reason="manual",
        )
        assert pc.desired_locked is True
        assert pc.manual_lock_until is not None

        # Теперь разблокируем
        await pc_service.unlock_pc(db_session, transport_mock, settings, pc)

        assert pc.desired_locked is False
        assert pc.desired_lock_reason is None
        assert pc.manual_lock_until is None

        # И была отправлена команда unlock
        args = transport_mock.send_pc_command.call_args_list[-1][0]
        assert args[1]["action"] == "unlock"


class TestAddTime:
    """Честная история: бонус отдельно, факт отдельно."""

    @pytest.mark.asyncio()
    async def test_add_time_increases_bonus_not_active(self, settings, db_session, mocker):
        """add_time увеличивает bonus_seconds, не трогая active_seconds."""
        pc = await create_test_pc(db_session, name="bonus_pc")
        usage = UsageDaily(
            pc_id=pc.id,
            usage_date=_today(settings),
            active_seconds=3600,
            locked_seconds=0,
        )
        db_session.add(usage)
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.add_time(db_session, transport_mock, settings, pc, minutes=30)
        await db_session.commit()

        assert usage.bonus_seconds == 1800
        assert usage.active_seconds == 3600  # факт не тронут

    @pytest.mark.asyncio()
    async def test_add_time_does_not_unlock_schedule_lock(self, settings, db_session, mocker):
        """Бонус не снимает блокировку по расписанию."""
        pc = await create_test_pc(db_session, name="bonus_sched_pc")
        pc.desired_locked = True
        pc.desired_lock_reason = "schedule"
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.add_time(db_session, transport_mock, settings, pc, minutes=30)
        await db_session.commit()

        assert pc.desired_locked is True
        # Только команда add_time, без unlock
        assert transport_mock.send_pc_command.call_count == 1
        assert transport_mock.send_pc_command.call_args[0][1]["action"] == "add_time"

    @pytest.mark.asyncio()
    async def test_add_time_does_not_unlock_manual_lock(self, settings, db_session, mocker):
        """Бонус не снимает ручную блокировку."""
        pc = await create_test_pc(db_session, name="bonus_manual_pc")
        pc.desired_locked = True
        pc.desired_lock_reason = "manual"
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.add_time(db_session, transport_mock, settings, pc, minutes=30)
        await db_session.commit()

        assert pc.desired_locked is True
        assert transport_mock.send_pc_command.call_count == 1

    @pytest.mark.asyncio()
    async def test_add_time_unlocks_daily_limit_lock(self, settings, db_session, mocker):
        """Бонус снимает блокировку по лимиту, если лимит больше не превышен."""
        pc = await create_test_pc(db_session, name="bonus_limit_pc")
        pc.daily_limit_minutes = 60
        pc.desired_locked = True
        pc.desired_lock_reason = "daily_limit"
        usage = UsageDaily(
            pc_id=pc.id,
            usage_date=_today(settings),
            active_seconds=61 * 60,
            locked_seconds=0,
        )
        db_session.add(usage)
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.add_time(db_session, transport_mock, settings, pc, minutes=30)
        await db_session.commit()

        assert pc.desired_locked is False
        actions = [c[0][1]["action"] for c in transport_mock.send_pc_command.call_args_list]
        assert actions == ["add_time", "unlock"]


class TestServerCommands:
    """Управление сервером через server/cmd."""

    @pytest.mark.asyncio()
    async def test_server_lock_command(self, settings, db_session, mocker):
        pc = await create_test_pc(db_session, name="srv_lock_pc")
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_server_command(
            db_session, transport_mock, settings, {"action": "lock", "pc": "srv_lock_pc"}
        )

        assert pc.desired_locked is True
        assert transport_mock.send_pc_command.call_count == 1
        assert transport_mock.send_pc_command.call_args[0][1]["action"] == "lock_now"

    @pytest.mark.asyncio()
    async def test_server_unlock_command(self, settings, db_session, mocker):
        pc = await create_test_pc(db_session, name="srv_unlock_pc")
        pc.desired_locked = True
        pc.desired_lock_reason = "manual"
        await db_session.commit()
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_server_command(
            db_session, transport_mock, settings, {"action": "unlock", "pc": "srv_unlock_pc"}
        )

        assert pc.desired_locked is False
        assert transport_mock.send_pc_command.call_args[0][1]["action"] == "unlock"

    @pytest.mark.asyncio()
    async def test_server_add_time_command(self, settings, db_session, mocker):
        pc = await create_test_pc(db_session, name="srv_time_pc")
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_server_command(
            db_session,
            transport_mock,
            settings,
            {"action": "add_time", "pc": "srv_time_pc", "minutes": 10},
        )

        cmd = transport_mock.send_pc_command.call_args[0][1]
        assert cmd == {"action": "add_time", "minutes": 10}

        # Бонус реально начислился
        usage = await pc_service.get_usage_today(db_session, settings, pc.id)
        assert usage.bonus_seconds == 600

    @pytest.mark.asyncio()
    async def test_server_command_unknown_pc(self, settings, db_session, mocker):
        """Неизвестный ПК — warning, без действий и падений."""
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_server_command(db_session, transport_mock, settings, {"action": "lock", "pc": "nope"})

        transport_mock.send_pc_command.assert_not_called()

    @pytest.mark.asyncio()
    async def test_server_command_unknown_action(self, settings, db_session, mocker):
        pc = await create_test_pc(db_session, name="srv_bad_action_pc")
        transport_mock = mocker.AsyncMock()

        await pc_service.handle_server_command(
            db_session, transport_mock, settings, {"action": "reboot", "pc": pc.name}
        )

        transport_mock.send_pc_command.assert_not_called()


class TestBuildServerStatus:
    """Статус сервера для внешних приложений."""

    @pytest.mark.asyncio()
    async def test_status_contains_pc_summary(self, settings, db_session):
        pc = await create_test_pc(db_session, name="status_pc")

        status = await pc_service.build_server_status(db_session, settings)

        assert status["online"] is True
        assert "status_pc" in status["pcs"]
        entry = status["pcs"]["status_pc"]
        assert entry["is_online"] is False
        assert entry["is_idle"] is False
        assert entry["active_seconds"] == 0
        assert entry["idle_seconds"] == 0
        assert entry["bonus_seconds"] == 0
        # лимит по умолчанию 120 мин → 7200 сек
        assert entry["limit_seconds"] == pc.daily_limit_minutes * 60
