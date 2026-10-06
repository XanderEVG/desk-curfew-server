"""Интеграционные тесты для POST /api/agent/hb."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_auth import hash_agent_token
from app.core.agent_transport import InMemoryAgentTransport
from app.database import get_db
from app.deps import get_agent_transport
from app.models import PC, PcEvent

RAW_TOKEN = "test_agent_token_12345"


@pytest_asyncio.fixture
async def transport() -> InMemoryAgentTransport:
    return InMemoryAgentTransport()


@pytest_asyncio.fixture
async def test_client(db_session: AsyncSession, transport: InMemoryAgentTransport) -> AsyncGenerator[AsyncClient, None]:
    from app.main import app

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_agent_transport] = lambda: transport

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def pc_with_token(db_session: AsyncSession) -> PC:
    pc = PC(
        name="agent_test_pc",
        display_name="Agent Test PC",
        daily_limit_minutes=120,
        agent_token_hash=hash_agent_token(RAW_TOKEN),
        is_active=True,
    )
    db_session.add(pc)
    await db_session.commit()
    await db_session.refresh(pc)
    return pc


class TestAgentHeartbeat:
    @pytest.mark.asyncio()
    async def test_missing_token_returns_401(self, test_client, pc_with_token):
        """Запрос без Authorization header — 401."""
        response = await test_client.post("/api/agent/hb", json={"locked": False})
        assert response.status_code == 401

    @pytest.mark.asyncio()
    async def test_invalid_token_returns_401(self, test_client, pc_with_token):
        """Неверный токен — 401."""
        response = await test_client.post(
            "/api/agent/hb",
            json={"locked": False},
            headers={"Authorization": "Bearer wrong_token"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio()
    async def test_valid_token_returns_commands(self, test_client, pc_with_token, transport):
        """Валидный токен — 200, команды из очереди."""
        await transport.send_pc_command("agent_test_pc", {"action": "lock_now", "reason": "manual"})

        response = await test_client.post(
            "/api/agent/hb",
            json={"locked": False},
            headers={"Authorization": f"Bearer {RAW_TOKEN}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["commands"]) == 1
        assert data["commands"][0]["action"] == "lock_now"
        assert "server_time" in data

    @pytest.mark.asyncio()
    async def test_heartbeat_updates_pc_state(self, test_client, pc_with_token, db_session):
        """Heartbeat обновляет is_online, last_seen_at."""
        response = await test_client.post(
            "/api/agent/hb",
            json={"locked": False, "active_user": "kid1", "idle_seconds": 0},
            headers={"Authorization": f"Bearer {RAW_TOKEN}"},
        )
        assert response.status_code == 200

        await db_session.refresh(pc_with_token)
        assert pc_with_token.is_online is True
        assert pc_with_token.last_seen_at is not None
        assert pc_with_token.last_active_user == "kid1"

    @pytest.mark.asyncio()
    async def test_events_are_stored(self, test_client, pc_with_token, db_session):
        """События из массива events сохраняются в PcEvent."""
        response = await test_client.post(
            "/api/agent/hb",
            json={
                "locked": True,
                "events": [
                    {"event": "locked", "reason": "daily_limit"},
                    {"event": "pin_attempt"},
                ],
            },
            headers={"Authorization": f"Bearer {RAW_TOKEN}"},
        )
        assert response.status_code == 200

        result = await db_session.scalars(PcEvent.__table__.select().where(PcEvent.pc_id == pc_with_token.id))
        events = result.all() if hasattr(result, "all") else []
        # Используем прямой запрос
        from sqlalchemy import select

        result = await db_session.scalars(select(PcEvent).where(PcEvent.pc_id == pc_with_token.id).order_by(PcEvent.id))
        events = list(result.all())
        assert len(events) == 2
        assert events[0].event_type == "locked"
        assert events[1].event_type == "pin_attempt"

    @pytest.mark.asyncio()
    async def test_drain_clears_queue(self, test_client, pc_with_token, transport):
        """После drain очередь пуста — следующий hb без новых команд возвращает []."""
        await transport.send_pc_command("agent_test_pc", {"action": "unlock"})

        response1 = await test_client.post(
            "/api/agent/hb",
            json={"locked": True},
            headers={"Authorization": f"Bearer {RAW_TOKEN}"},
        )
        assert len(response1.json()["commands"]) == 1

        response2 = await test_client.post(
            "/api/agent/hb",
            json={"locked": False},
            headers={"Authorization": f"Bearer {RAW_TOKEN}"},
        )
        assert response2.json()["commands"] == []

    @pytest.mark.asyncio()
    async def test_inactive_pc_rejected(self, db_session, test_client, transport):
        """Неактивный ПК (is_active=False) не пропускается по токену."""
        pc = PC(
            name="inactive_pc",
            display_name="Inactive",
            agent_token_hash=hash_agent_token("inactive_token"),
            is_active=False,
        )
        db_session.add(pc)
        await db_session.commit()

        response = await test_client.post(
            "/api/agent/hb",
            json={"locked": False},
            headers={"Authorization": "Bearer inactive_token"},
        )
        assert response.status_code == 401
