from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PCRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str

    daily_limit_minutes: int
    warning_before_lock_seconds: int

    is_active: bool

    is_online: bool
    is_locked: bool
    lock_reason: str | None

    desired_locked: bool
    desired_lock_reason: str | None

    last_seen_at: datetime | None
    last_active_user: str | None


class LoginRequest(BaseModel):
    username: str
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_active: bool
    created_at: datetime


class LockRequest(BaseModel):
    reason: str = "manual"
    delay_seconds: int = Field(default=0, ge=0, le=3600)


class AddTimeRequest(BaseModel):
    minutes: int = Field(default=30, gt=0, le=600)


class MessageResponse(BaseModel):
    ok: bool
    message: str | None = None
