from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import pc_service
from app.database import get_db
from app.schemas import StatsResponse

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
async def get_stats(db: Annotated[AsyncSession, Depends(get_db)]):
    """Общая статистика по всем ПК."""
    settings = get_settings()
    return await pc_service.get_stats(db, settings)
