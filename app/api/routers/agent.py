"""HTTP-эндпоинт для heartbeat-ов агентов.

Агенты поллют POST /api/agent/hb с Bearer-токеном; сервер возвращает
накопленные команды. Авторизация — по токену (без cookie-сессии).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pc_service
from app.core.agent_auth import get_pc_by_agent_token
from app.core.agent_transport import AgentTransport
from app.database import get_db
from app.deps import get_agent_transport
from app.schemas import AgentHeartbeatRequest, AgentHeartbeatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer ") :].strip()
    return token or None


@router.post("/hb", response_model=AgentHeartbeatResponse)
async def agent_heartbeat(
    request: Request,
    payload: AgentHeartbeatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    transport: Annotated[AgentTransport, Depends(get_agent_transport)],
) -> AgentHeartbeatResponse:
    """Heartbeat от агента: обновляет состояние ПК, принимает события,
    возвращает накопленные команды."""
    raw_token = _extract_bearer_token(request)
    if raw_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    pc = await get_pc_by_agent_token(db, raw_token)
    if pc is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown agent token")

    # События — раньше handle_heartbeat, чтобы is_locked был актуальным для resend-логики.
    for event in payload.events:
        await pc_service.handle_event(
            db,
            pc.name,
            {"event": event.event, "reason": event.reason},
        )

    await pc_service.handle_heartbeat(
        db,
        transport,
        _get_settings(),
        pc.name,
        {
            "locked": payload.locked,
            "lock_reason": payload.lock_reason,
            "idle_seconds": payload.idle_seconds,
            "active_user": payload.active_user,
        },
    )

    await db.commit()

    commands = transport.drain(pc.name)
    return AgentHeartbeatResponse(
        commands=commands,
        server_time=datetime.now(UTC),
    )


def _get_settings():
    from app.config import get_settings

    return get_settings()
