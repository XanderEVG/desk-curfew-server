"""Общие фикстуры для тестов."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base

# Тестовая БД запускается через: docker compose --profile test up -d test-db
TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5433/test"


@pytest.fixture()
def settings():
    """Создаёт тестовые настройки."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        database_url_sync="postgresql://test:test@localhost:5433/test",
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username="test",
        mqtt_password="test",
        mqtt_topic_prefix="test/curfew/kids",
        server_timezone="Asia/Yekaterinburg",
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Создаёт сессию к тестовой PostgreSQL БД.
    Перед запуском тестов: docker compose --profile test up -d test-db
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
