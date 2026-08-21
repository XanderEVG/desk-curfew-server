"""Тесты для бизнес-логики управления ПК."""
from datetime import datetime, time

import pytest
from freezegun import freeze_time
from zoneinfo import ZoneInfo

from app.config import Settings
from app.core.pc_service import get_end_of_day, is_allowed_now


@pytest.fixture()
def settings():
    """Создаёт тестовые настройки."""
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        database_url_sync="postgresql://test:test@localhost/test",
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username="test",
        mqtt_password="test",
        mqtt_topic_prefix="test/curfew/kids",
        server_timezone="Asia/Bishkek",
    )


class TestGetEndOfDay:
    """Тесты для функции get_end_of_day."""

    @freeze_time("2026-08-21 14:30:00", tz_offset=6)  # Asia/Bishkek = UTC+6
    def test_end_of_day_same_day(self, settings):
        """Конец дня должен быть в 00:00 следующего дня в локальной таймзоне."""
        now_utc = datetime(2026, 8, 21, 8, 30, 0)  # 14:30 в Bishkek
        end = get_end_of_day(settings, now_utc)

        # Конец дня в Bishkek = 00:00 22 августа = 18:00 UTC 21 августа
        expected = datetime(2026, 8, 21, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert end == expected

    @freeze_time("2026-08-21 23:59:00", tz_offset=6)
    def test_end_of_day_near_midnight(self, settings):
        """Даже в 23:59 конец дня должен быть в 00:00 следующего дня."""
        now_utc = datetime(2026, 8, 21, 17, 59, 0)  # 23:59 в Bishkek
        end = get_end_of_day(settings, now_utc)

        expected = datetime(2026, 8, 21, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
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
