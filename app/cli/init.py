import asyncio

import typer
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.seed import seed_pcs
from app.database import AsyncSessionLocal
from app.models import User


def init_command() -> None:
    """Полная инициализация: создать админа (если нет) и загрузить фикстуры."""

    async def _init() -> None:
        async with AsyncSessionLocal() as session:
            # Создаём админа, если ещё нет
            existing = await session.scalar(select(User).where(User.username == "admin"))

            if existing:
                typer.echo("Пользователь 'admin' уже существует, пропускаем")
            else:
                password = typer.prompt(
                    "Пароль для admin",
                    hide_input=True,
                    confirmation_prompt=True,
                )
                user = User(
                    username="admin",
                    password_hash=hash_password(password),
                    is_active=True,
                )
                session.add(user)
                await session.commit()
                typer.echo("Пользователь 'admin' создан")

            # Загружаем фикстуры
            await seed_pcs(session)
            typer.echo("Фикстуры загружены")

    asyncio.run(_init())
