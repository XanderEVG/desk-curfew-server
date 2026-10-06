"""Авторизация агентов по Bearer-токену.

Токен — случайная строка (secrets.token_urlsafe(32)), хеш хранится в
`PC.agent_token_hash` (bcrypt, как у паролей пользователей). Raw-токен
показывается один раз при генерации и кладётся в конфиг агента.
"""

from __future__ import annotations

import secrets

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PC


def generate_agent_token() -> str:
    """Сгенерировать raw-токен для агента (показать один раз)."""
    return secrets.token_urlsafe(32)


def hash_agent_token(raw_token: str) -> str:
    """bcrypt-хеш токена для хранения в БД."""
    token_bytes = raw_token.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    hashed: str = bcrypt.hashpw(token_bytes, salt).decode("utf-8")
    return hashed


def verify_agent_token(raw_token: str, token_hash: str) -> bool:
    """Сравнить raw-токен с хешем."""
    token_bytes = raw_token.encode("utf-8")[:72]
    result: bool = bcrypt.checkpw(token_bytes, token_hash.encode("utf-8"))
    return result


async def get_pc_by_agent_token(session: AsyncSession, raw_token: str) -> PC | None:
    """Найти ПК по raw-токену (перебор хешей; ПК мало — линейно ок)."""
    result = await session.scalars(select(PC).where(PC.agent_token_hash.is_not(None), PC.is_active.is_(True)))
    for pc in result.all():
        if pc.agent_token_hash and verify_agent_token(raw_token, pc.agent_token_hash):
            return pc
    return None
