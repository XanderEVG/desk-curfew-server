import logging
from datetime import time
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PC, ScheduleSlot

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures"


async def seed_pcs(session: AsyncSession) -> None:
    pcs_file = FIXTURES_DIR / "pcs.yaml"
    if not pcs_file.exists():
        logger.warning("Fixture file not found: %s", pcs_file)
        return

    with open(pcs_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    for pc_data in data.get("pcs", []):
        name = pc_data["name"]
        existing = await session.scalar(select(PC).where(PC.name == name))

        if existing:
            logger.info("PC %s already exists, skipping", name)
            continue

        pc = PC(
            name=name,
            display_name=pc_data.get("display_name", name),
            daily_limit_minutes=pc_data.get("daily_limit_minutes", 120),
            warning_before_lock_seconds=pc_data.get("warning_before_lock_seconds", 300),
        )
        session.add(pc)
        await session.flush()

        for slot_data in pc_data.get("schedule", []):
            allowed_from = time.fromisoformat(slot_data["allowed_from"])
            allowed_until = time.fromisoformat(slot_data["allowed_until"])
            session.add(
                ScheduleSlot(
                    pc_id=pc.id,
                    days=slot_data.get("days", []),
                    allowed_from=allowed_from,
                    allowed_until=allowed_until,
                    is_active=True,
                )
            )

        logger.info("Created PC %s with schedule", name)

    await session.commit()