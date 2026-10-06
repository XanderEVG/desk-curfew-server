"""Тесты для agent_auth."""

import pytest

from app.core.agent_auth import generate_agent_token, hash_agent_token, verify_agent_token
from app.models import PC


class TestAgentToken:
    def test_generate_token_returns_string(self):
        """generate_agent_token возвращает непустую строку."""
        token = generate_agent_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_generate_token_unique(self):
        """Каждый вызов generate_agent_token даёт уникальный токен."""
        token1 = generate_agent_token()
        token2 = generate_agent_token()
        assert token1 != token2

    def test_hash_and_verify(self):
        """hash_agent_token + verify_agent_token работают корректно."""
        raw_token = "test_token_12345"
        token_hash = hash_agent_token(raw_token)

        assert verify_agent_token(raw_token, token_hash) is True
        assert verify_agent_token("wrong_token", token_hash) is False

    def test_hash_is_bcrypt(self):
        """Хеш начинается с $2b$ (bcrypt)."""
        token_hash = hash_agent_token("test_token")
        assert token_hash.startswith("$2b$")


class TestGetPcByAgentToken:
    @pytest.mark.asyncio()
    async def test_find_pc_by_token(self, db_session):
        """get_pc_by_agent_token находит ПК по валидному токену."""
        from app.core.agent_auth import get_pc_by_agent_token, hash_agent_token

        raw_token = "test_token_for_pc"
        pc = PC(
            name="test_pc_token",
            display_name="Test PC",
            agent_token_hash=hash_agent_token(raw_token),
        )
        db_session.add(pc)
        await db_session.commit()

        found_pc = await get_pc_by_agent_token(db_session, raw_token)
        assert found_pc is not None
        assert found_pc.name == "test_pc_token"

    @pytest.mark.asyncio()
    async def test_return_none_for_invalid_token(self, db_session):
        """get_pc_by_agent_token возвращает None для неверного токена."""
        from app.core.agent_auth import get_pc_by_agent_token, hash_agent_token

        pc = PC(
            name="test_pc_token2",
            display_name="Test PC",
            agent_token_hash=hash_agent_token("correct_token"),
        )
        db_session.add(pc)
        await db_session.commit()

        found_pc = await get_pc_by_agent_token(db_session, "wrong_token")
        assert found_pc is None

    @pytest.mark.asyncio()
    async def test_return_none_for_pc_without_token(self, db_session):
        """get_pc_by_agent_token возвращает None для ПК без токена."""
        from app.core.agent_auth import get_pc_by_agent_token

        pc = PC(name="test_pc_no_token", display_name="Test PC")
        db_session.add(pc)
        await db_session.commit()

        found_pc = await get_pc_by_agent_token(db_session, "any_token")
        assert found_pc is None
