from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import pc_service
from app.core.agent_transport import AgentTransport
from app.database import get_db
from app.deps import get_agent_transport
from app.schemas import AddTimeRequest, LockRequest, MessageResponse

router = APIRouter(prefix="/api/pcs", tags=["control"])


@router.post("/{pc_name}/lock", response_model=MessageResponse)
async def lock_pc(
    pc_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    transport: Annotated[AgentTransport, Depends(get_agent_transport)],
    payload: LockRequest | None = None,
) -> MessageResponse:
    settings = get_settings()

    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    reason = payload.reason if payload else "manual"
    delay_seconds = payload.delay_seconds if payload else 0

    await pc_service.lock_pc(
        db,
        transport,
        settings,
        pc,
        reason=reason,
        delay_seconds=delay_seconds,
    )

    await db.commit()

    return MessageResponse(ok=True, message="Lock command sent")


@router.post("/{pc_name}/unlock", response_model=MessageResponse)
async def unlock_pc(
    pc_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    transport: Annotated[AgentTransport, Depends(get_agent_transport)],
) -> MessageResponse:
    settings = get_settings()

    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    await pc_service.unlock_pc(
        db,
        transport,
        settings,
        pc,
        reason="manual",
    )

    await db.commit()

    return MessageResponse(ok=True, message="Unlock command sent")


@router.post("/{pc_name}/add-time", response_model=MessageResponse)
async def add_time(
    pc_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    transport: Annotated[AgentTransport, Depends(get_agent_transport)],
    payload: AddTimeRequest | None = None,
) -> MessageResponse:
    settings = get_settings()

    pc = await pc_service.get_pc_by_name(db, pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    await pc_service.add_time(
        db,
        transport,
        settings,
        pc,
        minutes=payload.minutes if payload else 10,
    )

    await db.commit()

    return MessageResponse(ok=True, message="Time added")
