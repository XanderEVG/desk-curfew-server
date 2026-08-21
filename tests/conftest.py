"""Общие фикстуры для тестов."""
import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base


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
        server_timezone="Asia/Yekaterinburg",
    )


@pytest.fixture(scope="session")
def event_loop():
    """Создаёт event loop для всех тестов."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Создаёт in-memory SQLite базу для каждого теста."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
