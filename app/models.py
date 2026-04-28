from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Client(TimestampMixin, Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(180))
    subtitle: Mapped[str | None] = mapped_column(String(240))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    events: Mapped[list["Event"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    workers: Mapped[list["Worker"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(180))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), default="admin")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Worker(TimestampMixin, Base):
    __tablename__ = "workers"
    __table_args__ = (UniqueConstraint("client_id", "employee_number", name="uq_workers_client_employee_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    employee_number: Mapped[str] = mapped_column(String(40))
    display_code: Mapped[str | None] = mapped_column(String(60), index=True)
    source: Mapped[str] = mapped_column(String(30), default="platform", index=True)
    external_id: Mapped[str | None] = mapped_column(String(80), index=True)
    full_name: Mapped[str] = mapped_column(String(180), index=True)
    area: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    mobile: Mapped[str | None] = mapped_column(String(40))
    social: Mapped[str | None] = mapped_column(String(120))
    bank: Mapped[str | None] = mapped_column(String(120))
    account_number: Mapped[str | None] = mapped_column(String(80))
    clabe: Mapped[str | None] = mapped_column(String(18))
    ine_filename: Mapped[str | None] = mapped_column(String(255))
    ine_gcs_uri: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped["Client"] = relationship(back_populates="workers")
    assignments: Mapped[list["ShiftAssignment"]] = relationship(back_populates="worker", cascade="all, delete-orphan")


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(220), index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    event_type: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    salary_before: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("320.00"))
    salary_during: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("480.00"))
    salary_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("360.00"))
    supervisor_salary_before: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("450.00"))
    supervisor_salary_during: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("650.00"))
    supervisor_salary_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("500.00"))
    operator_positions: Mapped[int] = mapped_column(Integer, default=0)
    supervisor_positions: Mapped[int] = mapped_column(Integer, default=0)
    supervisor_before: Mapped[bool] = mapped_column(Boolean, default=True)
    supervisor_during: Mapped[bool] = mapped_column(Boolean, default=True)
    supervisor_after: Mapped[bool] = mapped_column(Boolean, default=False)

    client: Mapped["Client"] = relationship(back_populates="events")
    assignments: Mapped[list["ShiftAssignment"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class ShiftAssignment(TimestampMixin, Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (UniqueConstraint("event_id", "worker_id", "shift", name="uq_assignment_event_worker_shift"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id", ondelete="CASCADE"), index=True)
    shift: Mapped[str] = mapped_column(String(20), index=True)
    worker_role: Mapped[str] = mapped_column(String(20), default="jornal", index=True)
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    event: Mapped["Event"] = relationship(back_populates="assignments")
    worker: Mapped["Worker"] = relationship(back_populates="assignments")
