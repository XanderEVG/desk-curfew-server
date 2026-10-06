"""Тесты планировщика (scheduler)."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from app.config import Settings
from app.core.agent_transport import AgentTransport
from app.core.scheduler import process_pc, tick_with_session
from app.models import PC, ScheduleSlot, UsageDaily


def real_today(settings: Settings):
    """Реальная сегодняшняя дата в таймзоне сервера."""
    return datetime.now(timezone.utc).astimezone(ZoneInfo(settings.server_timezone)).date()


@pytest_asyncio.fixture
async def transport_mock() -> AsyncMock:
    """Мок транспорта для команд агентам."""
    return AsyncMock(spec=AgentTransport)


@pytest_asyncio.fixture
async def test_pc(db_session: AsyncSession, now_utc: datetime) -> PC:
    """Создаёт тестовый ПК."""
    pc = PC(
        name="test_pc",
        display_name="Test PC",
        daily_limit_minutes=120,
        warning_before_lock_seconds=300,
        is_active=True,
        is_online=True,
        last_seen_at=now_utc,
    )
    db_session.add(pc)
    await db_session.commit()
    await db_session.refresh(pc)
    return pc


@pytest.fixture()
def now_utc() -> datetime:
    """Текущее время UTC."""
    return datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)  # Понедельник


@pytest.fixture()
def local_now(settings: Settings, now_utc: datetime) -> datetime:
    """Локальное время."""
    return now_utc.astimezone(ZoneInfo(settings.server_timezone))


class TestOfflineDetection:
    """Тесты определения оффлайн-статуса."""

    @pytest.mark.asyncio()
    async def test_pc_offline_by_heartbeat_timeout(
        self, db_session, settings, transport_mock, test_pc, now_utc, local_now
    ):
        """ПК помечается оффлайн, если heartbeat старше timeout."""
        test_pc.last_seen_at = now_utc - timedelta(seconds=120)
        test_pc.is_online = True
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)
        await db_session.commit()
        await db_session.refresh(test_pc)

        assert test_pc.is_online is False
        transport_mock.send_pc_command.assert_not_called()

    @pytest.mark.asyncio()
    async def test_pc_online_with_recent_heartbeat(
        self, db_session, settings, transport_mock, test_pc, now_utc, local_now
    ):
        """ПК остаётся онлайн, если heartbeat свежий."""
        test_pc.last_seen_at = now_utc - timedelta(seconds=30)
        test_pc.is_online = True
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)
        await db_session.commit()
        await db_session.refresh(test_pc)

        assert test_pc.is_online is True

    @pytest.mark.asyncio()
    async def test_skip_offline_pc(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Оффлайн ПК пропускается — никаких команд."""
        test_pc.is_online = False
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)

        transport_mock.send_pc_command.assert_not_called()


class TestDailyLimit:
    """Тесты дневного лимита."""

    @pytest.mark.asyncio()
    async def test_lock_by_daily_limit(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Блокировка при превышении дневного лимита."""
        test_pc.daily_limit_minutes = 60  # 60 минут
        test_pc.desired_locked = False
        await db_session.commit()

        usage = UsageDaily(
            pc_id=test_pc.id,
            usage_date=real_today(settings),
            active_seconds=61 * 60,  # 61 минута > 60 минут лимит
            locked_seconds=0,
        )
        db_session.add(usage)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)
        await db_session.commit()
        await db_session.refresh(test_pc)

        assert test_pc.desired_locked is True
        assert test_pc.desired_lock_reason == "daily_limit"
        transport_mock.send_pc_command.assert_called_once()
        call_args = transport_mock.send_pc_command.call_args[0]
        assert call_args[1]["action"] == "lock_in"
        assert call_args[1]["reason"] == "daily_limit"

    @pytest.mark.asyncio()
    async def test_no_lock_under_daily_limit(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Нет блокировки, если лимит не превышен."""
        test_pc.daily_limit_minutes = 120
        test_pc.desired_locked = False
        await db_session.commit()

        usage = UsageDaily(
            pc_id=test_pc.id,
            usage_date=real_today(settings),
            active_seconds=60 * 60,  # 60 минут < 120 минут лимит
            locked_seconds=0,
        )
        db_session.add(usage)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)

        transport_mock.send_pc_command.assert_not_called()


class TestSchedule:
    """Тесты расписания."""

    @pytest.mark.asyncio()
    async def test_lock_outside_schedule(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Блокировка вне расписания."""
        test_pc.desired_locked = False
        await db_session.commit()

        # Понедельник НЕ включён в дни слота
        slot = ScheduleSlot(
            pc_id=test_pc.id,
            days=["tue", "wed", "thu", "fri", "sat", "sun"],
            allowed_from=time(10, 0),
            allowed_until=time(20, 0),
            is_active=True,
        )
        db_session.add(slot)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)
        await db_session.commit()
        await db_session.refresh(test_pc)

        assert test_pc.desired_locked is True
        assert test_pc.desired_lock_reason == "schedule"

    @pytest.mark.asyncio()
    async def test_no_lock_within_schedule(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Нет блокировки в рамках расписания."""
        test_pc.desired_locked = False
        await db_session.commit()

        slot = ScheduleSlot(
            pc_id=test_pc.id,
            days=["mon", "tue", "wed", "thu", "fri"],
            allowed_from=time(0, 0),
            allowed_until=time(23, 59),
            is_active=True,
        )
        db_session.add(slot)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)

        transport_mock.send_pc_command.assert_not_called()


class TestUnlock:
    """Тесты разблокировки."""

    @pytest.mark.asyncio()
    async def test_unlock_when_schedule_allows(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Разблокировка, когда расписание разрешает."""
        test_pc.desired_locked = True
        test_pc.desired_lock_reason = "schedule"
        test_pc.is_locked = True
        await db_session.commit()

        slot = ScheduleSlot(
            pc_id=test_pc.id,
            days=["mon"],
            allowed_from=time(0, 0),
            allowed_until=time(23, 59),
            is_active=True,
        )
        db_session.add(slot)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)
        await db_session.commit()
        await db_session.refresh(test_pc)

        assert test_pc.desired_locked is False
        assert test_pc.desired_lock_reason is None
        transport_mock.send_pc_command.assert_called_once()
        call_args = transport_mock.send_pc_command.call_args[0]
        assert call_args[1]["action"] == "unlock"

    @pytest.mark.asyncio()
    async def test_no_unlock_for_manual_lock(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Нет автоматической разблокировки ручной блокировки."""
        test_pc.desired_locked = True
        test_pc.desired_lock_reason = "manual"
        test_pc.is_locked = True
        test_pc.manual_lock_until = now_utc + timedelta(hours=1)  # Ещё не истекла
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)

        transport_mock.send_pc_command.assert_not_called()


class TestManualLockExpiry:
    """Тесты истечения ручной блокировки."""

    @pytest.mark.asyncio()
    async def test_manual_lock_expired(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Ручная блокировка снимается по истечении."""
        test_pc.desired_locked = True
        test_pc.desired_lock_reason = "manual"
        test_pc.is_locked = True
        test_pc.manual_lock_until = now_utc - timedelta(seconds=1)  # Истекла
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)
        await db_session.commit()
        await db_session.refresh(test_pc)

        assert test_pc.desired_locked is False
        transport_mock.send_pc_command.assert_called_once()
        call_args = transport_mock.send_pc_command.call_args[0]
        assert call_args[1]["action"] == "unlock"
        assert call_args[1]["reason"] == "manual_lock_expired"


class TestResendLock:
    """Тесты повторной отправки команды блокировки."""

    @pytest.mark.asyncio()
    async def test_resend_lock_if_not_locked_by_agent(
        self, db_session, settings, transport_mock, test_pc, now_utc, local_now
    ):
        """Повторная отправка lock, если агент не заблокировал."""
        test_pc.desired_locked = True
        test_pc.desired_lock_reason = "schedule"
        test_pc.is_locked = False  # Агент не заблокировал
        test_pc.last_command_at = now_utc - timedelta(seconds=150)  # Прошло > 120 сек
        await db_session.commit()

        # Расписание запрещает (должен быть заблокирован)
        slot = ScheduleSlot(
            pc_id=test_pc.id,
            days=["tue"],  # Понедельник не включён
            allowed_from=time(10, 0),
            allowed_until=time(20, 0),
            is_active=True,
        )
        db_session.add(slot)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)

        transport_mock.send_pc_command.assert_called_once()
        call_args = transport_mock.send_pc_command.call_args[0]
        assert call_args[1]["action"] == "lock_now"  # Без задержки

    @pytest.mark.asyncio()
    async def test_no_resend_if_recent_command(self, db_session, settings, transport_mock, test_pc, now_utc, local_now):
        """Нет повторной отправки, если команда была недавно."""
        test_pc.last_seen_at = now_utc - timedelta(seconds=30)
        test_pc.desired_locked = True
        test_pc.desired_lock_reason = "schedule"
        test_pc.is_locked = False
        test_pc.last_command_at = now_utc - timedelta(seconds=60)  # Прошло < 120 сек
        await db_session.commit()

        slot = ScheduleSlot(
            pc_id=test_pc.id,
            days=["tue"],  # Понедельник не включён
            allowed_from=time(10, 0),
            allowed_until=time(20, 0),
            is_active=True,
        )
        db_session.add(slot)
        await db_session.commit()

        await process_pc(db_session, settings, transport_mock, test_pc, now_utc, local_now)

        transport_mock.send_pc_command.assert_not_called()


class TestTickWithSession:
    """Тесты tick_with_session."""

    @pytest.mark.asyncio()
    async def test_tick_processes_all_active_pcs(self, db_session, settings, transport_mock, now_utc):
        """tick_with_session обрабатывает все активные ПК."""
        pc1 = PC(name="pc1", display_name="PC 1", is_active=True, is_online=True, last_seen_at=now_utc)
        pc2 = PC(name="pc2", display_name="PC 2", is_active=True, is_online=True, last_seen_at=now_utc)
        pc3 = PC(
            name="pc3", display_name="PC 3", is_active=False, is_online=True, last_seen_at=now_utc
        )  # Неактивный — не должен обрабатываться
        db_session.add_all([pc1, pc2, pc3])
        await db_session.commit()

        await tick_with_session(db_session, settings, transport_mock)

        transport_mock.send_pc_command.assert_not_called()

    @pytest.mark.asyncio()
    async def test_tick_skips_offline_pcs(self, db_session, settings, transport_mock, now_utc):
        """tick_with_session пропускает оффлайн ПК."""
        pc_offline = PC(
            name="pc_offline",
            display_name="PC Offline",
            is_active=True,
            is_online=True,
            last_seen_at=now_utc - timedelta(seconds=120),  # Старше 90 сек
        )
        db_session.add(pc_offline)
        await db_session.commit()

        await tick_with_session(db_session, settings, transport_mock)
        await db_session.commit()
        await db_session.refresh(pc_offline)

        assert pc_offline.is_online is False
        transport_mock.send_pc_command.assert_not_called()
