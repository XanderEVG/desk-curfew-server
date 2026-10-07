"""Интеграционные тесты API."""

from collections.abc import AsyncGenerator
from datetime import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password
from app.database import get_db
from app.models import PC, ScheduleSlot, User


@pytest_asyncio.fixture
async def test_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Создаёт тестовый клиент FastAPI с подменой БД."""
    from app.main import app

    # Переопределяем зависимость get_db, чтобы использовать тестовую сессию
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    # Очищаем override после теста
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Создаёт тестового админа."""
    user = User(
        username="admin",
        password_hash=hash_password("testpass"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_pc(db_session: AsyncSession) -> PC:
    """Создаёт тестовый ПК."""
    pc = PC(
        name="test_pc",
        display_name="Test PC",
        daily_limit_minutes=120,
        warning_before_lock_seconds=300,
    )
    db_session.add(pc)
    await db_session.commit()
    await db_session.refresh(pc)
    return pc


class TestAuth:
    """Тесты авторизации."""

    @pytest.mark.asyncio
    async def test_login_success(self, test_client, admin_user):
        """Успешный логин возвращает пользователя и устанавливает cookie."""
        response = await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "admin"
        assert data["is_active"] is True

        # Проверяем, что cookie установлен
        assert "curfew_session" in response.cookies

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, test_client, admin_user):
        """Неправильный пароль возвращает 401."""
        response = await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "wrong"},
        )

        assert response.status_code == 401
        assert "curfew_session" not in response.cookies

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, test_client):
        """Логин несуществующего пользователя возвращает 401."""
        response = await test_client.post(
            "/auth/login",
            json={"username": "nobody", "password": "test"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_valid_session(self, test_client, admin_user):
        """GET /auth/me с валидной сессией возвращает пользователя."""
        # Логинимся
        login_response = await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )
        assert login_response.status_code == 200

        # Запрашиваем /me с теми же cookie
        me_response = await test_client.get("/auth/me")

        assert me_response.status_code == 200
        assert me_response.json()["username"] == "admin"

    @pytest.mark.asyncio
    async def test_me_without_session(self, test_client):
        """GET /auth/me без сессии возвращает 401."""
        response = await test_client.get("/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, test_client, admin_user):
        """Logout удаляет сессию и cookie."""
        # Логинимся
        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        # Logout
        logout_response = await test_client.post("/auth/logout")
        assert logout_response.status_code == 200

        # Проверяем, что сессия больше не работает
        me_response = await test_client.get("/auth/me")
        assert me_response.status_code == 401


class TestPcsApi:
    """Тесты API для ПК."""

    @pytest.mark.asyncio
    async def test_list_pcs_requires_auth(self, test_client, test_pc):
        """GET /api/pcs без авторизации возвращает 401."""
        response = await test_client.get("/api/pcs")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_pcs_with_auth(self, test_client, admin_user, test_pc):
        """GET /api/pcs с авторизацией возвращает список ПК."""
        # Логинимся
        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        response = await test_client.get("/api/pcs")
        assert response.status_code == 200

        data = response.json()
        assert len(data) >= 1
        assert any(pc["name"] == "test_pc" for pc in data)

    @pytest.mark.asyncio
    async def test_get_pc_with_auth(self, test_client, admin_user, test_pc):
        """GET /api/pcs/{name} возвращает конкретный ПК."""
        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        response = await test_client.get(f"/api/pcs/{test_pc.name}")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "test_pc"
        assert "usage_today" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_pc(self, test_client, admin_user):
        """GET /api/pcs/{name} для несуществующего ПК возвращает 404."""
        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        response = await test_client.get("/api/pcs/nonexistent")
        assert response.status_code == 404


class TestControlApi:
    """Тесты API для управления ПК."""

    @pytest.mark.asyncio
    async def test_lock_pc(self, test_client, admin_user, test_pc, mocker):
        """POST /api/pcs/{name}/lock отправляет команду."""
        # Мокаем транспорт
        from app.deps import get_agent_transport

        transport_mock = mocker.AsyncMock()

        # Получаем app из клиента и переопределяем зависимость
        app = test_client._transport.app  # type: ignore
        app.dependency_overrides[get_agent_transport] = lambda: transport_mock

        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        response = await test_client.post(f"/api/pcs/{test_pc.name}/lock")
        assert response.status_code == 200
        assert response.json()["ok"] is True

        # Проверяем, что команда отправлена
        transport_mock.send_pc_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_unlock_pc(self, test_client, admin_user, test_pc, mocker):
        """POST /api/pcs/{name}/unlock отправляет команду разблокировки."""
        from app.deps import get_agent_transport

        transport_mock = mocker.AsyncMock()
        app = test_client._transport.app  # type: ignore
        app.dependency_overrides[get_agent_transport] = lambda: transport_mock

        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        response = await test_client.post(f"/api/pcs/{test_pc.name}/unlock")
        assert response.status_code == 200

        args = transport_mock.send_pc_command.call_args[0]
        assert args[1]["action"] == "unlock"


class TestScheduleApi:
    """Тесты API для расписания."""

    @pytest.mark.asyncio
    async def test_get_schedule(self, test_client, admin_user, test_pc, db_session):
        """GET /api/pcs/{name}/schedule возвращает расписание."""
        # Добавляем слот
        slot = ScheduleSlot(
            pc_id=test_pc.id,
            days=["mon", "tue", "wed"],
            allowed_from=time(16, 0),
            allowed_until=time(20, 0),
            is_active=True,
        )
        db_session.add(slot)
        await db_session.commit()

        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        response = await test_client.get(f"/api/pcs/{test_pc.name}/schedule")
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 1
        assert data[0]["days"] == ["mon", "tue", "wed"]

    @pytest.mark.asyncio
    async def test_update_schedule(self, test_client, admin_user, test_pc):
        """PUT /api/pcs/{name}/schedule заменяет расписание."""
        await test_client.post(
            "/auth/login",
            json={"username": "admin", "password": "testpass"},
        )

        new_schedule = {
            "slots": [
                {
                    "days": ["sat", "sun"],
                    "allowed_from": "10:00",
                    "allowed_until": "21:00",
                    "is_active": True,
                }
            ]
        }

        response = await test_client.put(
            f"/api/pcs/{test_pc.name}/schedule",
            json=new_schedule,
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 1
        assert data[0]["days"] == ["sat", "sun"]
