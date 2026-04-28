from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    email: str
    password: str


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    subtitle: str | None = None


class WorkerIn(BaseModel):
    employee_number: str | None = None
    display_code: str | None = None
    worker_type: str = Field(default="jornal", pattern="^(jornal|supervisor)$")
    full_name: str = Field(min_length=1, max_length=180)
    area: str = Field(min_length=1, max_length=120)
    phone: str | None = None
    mobile: str | None = None
    social: str | None = None
    bank: str | None = None
    account_number: str | None = None
    clabe: str | None = Field(default=None, max_length=18)
    ine_filename: str | None = None


class WorkerOut(WorkerIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    employee_number: str
    source: str = "platform"
    external_id: str | None = None
    ine_gcs_uri: str | None = None


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=220)
    event_date: date
    event_type: str = Field(min_length=1, max_length=120)
    description: str | None = None
    salary_before: Decimal = Decimal("320.00")
    salary_during: Decimal = Decimal("480.00")
    salary_after: Decimal = Decimal("360.00")
    supervisor_salary_before: Decimal = Decimal("450.00")
    supervisor_salary_during: Decimal = Decimal("650.00")
    supervisor_salary_after: Decimal = Decimal("500.00")
    operator_positions: int = 0
    supervisor_positions: int = 0
    supervisor_before: bool = True
    supervisor_during: bool = True
    supervisor_after: bool = False
    schedule_before: str | None = None
    schedule_during: str | None = None
    schedule_after: str | None = None
    sub_event_name: str | None = None


class EventOut(EventIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int


class AssignmentIn(BaseModel):
    worker_id: int
    shift: str = Field(pattern="^(before|during|after)$")
    worker_role: str = Field(default="jornal", pattern="^(jornal|supervisor)$")


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    worker_id: int
    shift: str
    worker_role: str
    pay_amount: Decimal
    worker: WorkerOut


class SummaryRow(BaseModel):
    worker_id: int
    full_name: str
    area: str
    before_count: int
    during_count: int
    after_count: int
    shift_count: int
    supervisor_shift_count: int
    total_pay: Decimal


class SummaryOut(BaseModel):
    events: int
    active_workers: int
    total_shifts: int
    total_pay: Decimal
    rows: list[SummaryRow]
