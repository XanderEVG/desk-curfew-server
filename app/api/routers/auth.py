from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.auth import (
    authenticate_user,
    create_session,
    delete_session_cookie,
    get_current_user,
    set_session_cookie,
)
from app.database import get_db
from app.models import Session, User
from app.schemas import LoginRequest, UserRead

settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserRead)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user = await authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    session = await create_session(
        db,
        user,
        ip_address=request.client.host,
        user_agent=request.headers.get("user-agent"),
    )

    set_session_cookie(response, session.id)
    return user


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        session = await db.scalar(select(Session).where(Session.id == session_id))
        if session:
            await db.delete(session)
            await db.commit()

    delete_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserRead)
async def me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user
