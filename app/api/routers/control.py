from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import pc_service
from app.database import get_db
from app.deps import get_mqtt_service
from app.schemas import AddTimeRequest, LockRequest, MessageResponse

router = APIRouter(prefix="/api/pcs", tags=["control"])


@router.post("/{pc_name}/lock", response_model=MessageResponse)
async def lock_pc(
    pc_name: str,
    payload: LockRequest,
    db: AsyncSession = Depends(get_db),
    mqtt_service=Depends(get_mqtt_service),
):
    settings = get_settings()

    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    await pc_service.lock_pc(
        db,
        mqtt_service,
        settings,
        pc,
        reason=payload.reason,
        delay_seconds=payload.delay_seconds,
    )

    await db.commit()

    return MessageResponse(ok=True, message="Lock command sent")


@router.post("/{pc_name}/unlock", response_model=MessageResponse)
async def unlock_pc(
    pc_name: str,
    db: AsyncSession = Depends(get_db),
    mqtt_service=Depends(get_mqtt_service),
):
    settings = get_settings()

    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    await pc_service.unlock_pc(
        db,
        mqtt_service,
        settings,
        pc,
        reason="manual",
    )

    await db.commit()

    return MessageResponse(ok=True, message="Unlock command sent")


@router.post("/{pc_name}/add-time", response_model=MessageResponse)
async def add_time(
    pc_name: str,
    payload: AddTimeRequest,
    db: AsyncSession = Depends(get_db),
    mqtt_service=Depends(get_mqtt_service),
):
    settings = get_settings()

    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    await pc_service.add_time(
        db,
        mqtt_service,
        settings,
        pc,
        minutes=payload.minutes,
    )

    await db.commit()

    return MessageResponse(ok=True, message="Time added")