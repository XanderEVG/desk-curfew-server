import asyncio

import typer
from sqlalchemy import select

from app.core.auth import hash_password
from app.database import AsyncSessionLocal
from app.models import User


def create_user_command(
    username: str = typer.Argument(..., help="Имя пользователя"),
    password: str = typer.Option(
        ...,
        prompt=True,
        confirmation_prompt=True,
        hide_input=True,
        help="Пароль",
    ),
) -> None:
    """Создать нового пользователя."""

    async def _create() -> None:
        async with AsyncSessionLocal() as session:
            existing = await session.scalar(
                select(User).where(User.username == username)
            )
            if existing:
                typer.echo(f"Пользователь '{username}' уже существует")
                raise typer.Exit(code=1)

            user = User(
                username=username,
                password_hash=hash_password(password),
                is_active=True,
            )
            session.add(user)
            await session.commit()
            typer.echo(f"Пользователь '{username}' создан")

    asyncio.run(_create())
