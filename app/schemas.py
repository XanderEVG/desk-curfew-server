from datetime import date, datetime, time

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
    manual_lock_until: datetime | None

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


class UsageToday(BaseModel):
    """Использование времени за сегодня."""

    active_seconds: int
    locked_seconds: int
    limit_seconds: int
    usage_percent: float  # 0-100


class PCWithUsage(PCRead):
    """ПК + использование за сегодня (для дашборда)."""

    usage_today: UsageToday


class UsageDayRead(BaseModel):
    """Использование за один день (для графиков)."""

    model_config = ConfigDict(from_attributes=True)

    usage_date: date
    active_seconds: int
    locked_seconds: int


class PcEventRead(BaseModel):
    """Событие от агента."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    payload: dict | None
    created_at: datetime


class CommandLogRead(BaseModel):
    """Команда, отправленная на ПК."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    payload: dict | None
    created_at: datetime


class ScheduleSlotRead(BaseModel):
    """Слот расписания."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    days: list[str]
    allowed_from: time
    allowed_until: time
    is_active: bool


class ScheduleSlotWrite(BaseModel):
    """Слот расписания для записи."""

    days: list[str] = Field(..., min_length=1)
    allowed_from: time
    allowed_until: time
    is_active: bool = True


class ScheduleUpdateRequest(BaseModel):
    """Запрос на полное обновление расписания ПК."""

    slots: list[ScheduleSlotWrite]


class PCUpdateRequest(BaseModel):
    """Обновление настроек ПК."""

    display_name: str | None = None
    daily_limit_minutes: int | None = Field(default=None, ge=0)
    warning_before_lock_seconds: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class StatsResponse(BaseModel):
    """Общая статистика по всем ПК."""

    total_pcs: int
    online_pcs: int
    locked_pcs: int
    total_active_seconds_today: int
