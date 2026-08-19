from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PC
from app.schemas import PCRead
from app.core.auth import get_current_user

router = APIRouter(
    prefix="/api/pcs",
    tags=["pcs"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[PCRead])
async def list_pcs(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(PC).order_by(PC.id))
    return result.all()


@router.get("/{pc_name}", response_model=PCRead)
async def get_pc(pc_name: str, db: AsyncSession = Depends(get_db)):
    pc = await db.scalar(select(PC).where(PC.name == pc_name))

    if pc is None:
        raise HTTPException(status_code=404, detail="PC not found")

    return pc