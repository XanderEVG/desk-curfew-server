from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PC(Base):
    __tablename__ = "pcs"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))

    daily_limit_minutes: Mapped[int] = mapped_column(Integer, default=120)
    warning_before_lock_seconds: Mapped[int] = mapped_column(Integer, default=300)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Фактическое состояние, известное серверу
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_lock_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Желаемое состояние, которое сервер хочет установить
    desired_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    desired_lock_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_command_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_active_user: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    schedule_slots: Mapped[list[ScheduleSlot]] = relationship(
        back_populates="pc",
        cascade="all, delete-orphan",
    )


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(primary_key=True)

    pc_id: Mapped[int] = mapped_column(
        ForeignKey("pcs.id", ondelete="CASCADE"),
        index=True,
    )

    # Пример: ["mon", "tue", "wed", "thu", "fri"]
    days: Mapped[list] = mapped_column(JSONB, default=list)

    allowed_from: Mapped[time] = mapped_column(Time)
    allowed_until: Mapped[time] = mapped_column(Time)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    pc: Mapped[PC] = relationship(back_populates="schedule_slots")


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("pc_id", "usage_date", name="uq_usage_daily_pc_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    pc_id: Mapped[int] = mapped_column(
        ForeignKey("pcs.id", ondelete="CASCADE"),
        index=True,
    )

    usage_date: Mapped[date] = mapped_column(Date, index=True)

    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    locked_seconds: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class PcEvent(Base):
    __tablename__ = "pc_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    pc_id: Mapped[int] = mapped_column(
        ForeignKey("pcs.id", ondelete="CASCADE"),
        index=True,
    )

    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CommandLog(Base):
    __tablename__ = "command_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    pc_id: Mapped[int] = mapped_column(
        ForeignKey("pcs.id", ondelete="CASCADE"),
        index=True,
    )

    action: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # случайный токен
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
