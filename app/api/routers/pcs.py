from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import pc_service
from app.database import get_db
from app.models import PC
from app.schemas import (
    CommandLogRead,
    PcEventRead,
    PCRead,
    PCUpdateRequest,
    PCWithUsage,
    ScheduleSlotRead,
    ScheduleUpdateRequest,
    UsageDayRead,
)

router = APIRouter(prefix="/api/pcs", tags=["pcs"])


@router.get("", response_model=list[PCWithUsage])
async def list_pcs(db: Annotated[AsyncSession, Depends(get_db)]):
    """Список всех ПК с использованием времени за сегодня (для дашборда)."""
    settings = get_settings()
    result = await db.scalars(select(PC).order_by(PC.id))
    pcs = result.all()

    response = []
    for pc in pcs:
        data = await pc_service.get_pc_with_usage_today(db, settings, pc)
        response.append({**PCRead.model_validate(data["pc"]).model_dump(), "usage_today": data["usage_today"]})

    return response


@router.get("/{pc_name}", response_model=PCWithUsage)
async def get_pc(pc_name: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Информация о ПК + использование за сегодня."""
    settings = get_settings()
    pc = await pc_service.get_pc_by_name(db, pc_name)

    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    data = await pc_service.get_pc_with_usage_today(db, settings, pc)
    return {**PCRead.model_validate(data["pc"]).model_dump(), "usage_today": data["usage_today"]}


@router.patch("/{pc_name}", response_model=PCRead)
async def update_pc(
    pc_name: str,
    payload: PCUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Обновить настройки ПК (лимит времени, имя, активность)."""
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    if payload.display_name is not None:
        pc.display_name = payload.display_name
    if payload.daily_limit_minutes is not None:
        pc.daily_limit_minutes = payload.daily_limit_minutes
    if payload.warning_before_lock_seconds is not None:
        pc.warning_before_lock_seconds = payload.warning_before_lock_seconds
    if payload.is_active is not None:
        pc.is_active = payload.is_active

    await db.commit()
    await db.refresh(pc)
    return pc


@router.get("/{pc_name}/usage", response_model=list[UsageDayRead])
async def get_usage(
    pc_name: str,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """История использования за последние N дней."""
    settings = get_settings()
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    return await pc_service.get_usage_period(db, settings, pc.id, days)


@router.get("/{pc_name}/events", response_model=list[PcEventRead])
async def get_events(
    pc_name: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """Последние события от агента."""
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    return await pc_service.get_pc_events(db, pc.id, limit)


@router.get("/{pc_name}/commands", response_model=list[CommandLogRead])
async def get_commands(
    pc_name: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """История команд, отправленных на ПК."""
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    return await pc_service.get_pc_commands(db, pc.id, limit)


@router.get("/{pc_name}/schedule", response_model=list[ScheduleSlotRead])
async def get_schedule(
    pc_name: str,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """Текущее расписание ПК."""
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    return await pc_service.get_pc_schedule(db, pc.id)


@router.put("/{pc_name}/schedule", response_model=list[ScheduleSlotRead])
async def update_schedule(
    pc_name: str,
    payload: ScheduleUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """Полностью заменить расписание ПК."""
    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    slots_data = [slot.model_dump() for slot in payload.slots]
    await pc_service.update_pc_schedule(db, pc.id, slots_data)
    await db.commit()

    return await pc_service.get_pc_schedule(db, pc.id)
