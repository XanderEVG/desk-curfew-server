import asyncio

import typer

from app.core.seed import seed_pcs
from app.database import AsyncSessionLocal


def seed_command() -> None:
    """Загрузить фикстуры (ПК и расписание) из fixtures/pcs.yaml."""

    async def _seed() -> None:
        async with AsyncSessionLocal() as session:
            await seed_pcs(session)
            typer.echo("Фикстуры загружены")

    asyncio.run(_seed())