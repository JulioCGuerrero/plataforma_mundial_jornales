from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.models import Client, Event, ShiftAssignment, User, Worker
from app.schemas import (
    AssignmentIn,
    AssignmentOut,
    ClientOut,
    EventIn,
    EventOut,
    LoginIn,
    SummaryOut,
    SummaryRow,
    TokenOut,
    WorkerIn,
    WorkerOut,
)
from app.security import create_access_token, current_user, verify_password


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Control de Jornales API", version="1.0.0")
settings = get_settings()

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_client_or_404(db: Session, client_slug: str) -> Client:
    client = db.query(Client).filter(Client.slug == client_slug, Client.active.is_(True)).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return client


def next_platform_code(db: Session, client_id: int) -> str:
    workers = (
        db.query(Worker.display_code, Worker.employee_number)
        .filter(Worker.client_id == client_id, Worker.source == "platform")
        .all()
    )
    highest = 0
    for display_code, employee_number in workers:
        code = display_code or employee_number or ""
        if code.lower().startswith("fp-"):
            try:
                highest = max(highest, int(code.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
    return f"fp-{highest + 1:03d}"


def salary_for_shift(event: Event, shift: str, worker_role: str = "jornal") -> Decimal:
    if worker_role == "supervisor":
        return {
            "before": event.supervisor_salary_before,
            "during": event.supervisor_salary_during,
            "after": event.supervisor_salary_after,
        }[shift]
    return {
        "before": event.salary_before,
        "during": event.salary_during,
        "after": event.salary_after,
    }[shift]


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(1))
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = db.query(User).filter(User.email == payload.email, User.active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales invalidas")
    return TokenOut(access_token=create_access_token(user.email))


@app.get("/api/clients", response_model=list[ClientOut])
def list_clients(_: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Client]:
    return db.query(Client).filter(Client.active.is_(True)).order_by(Client.name).all()


@app.get("/api/clients/{client_slug}/workers", response_model=list[WorkerOut])
def list_workers(client_slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Worker]:
    client = get_client_or_404(db, client_slug)
    singa_client = db.query(Client).filter(Client.slug == "singa", Client.active.is_(True)).first()
    client_ids = [client.id]
    if singa_client:
        client_ids.append(singa_client.id)
    return (
        db.query(Worker)
        .filter(Worker.client_id.in_(client_ids), Worker.active.is_(True))
        .order_by(Worker.source, Worker.full_name)
        .all()
    )


@app.post("/api/clients/{client_slug}/workers", response_model=WorkerOut, status_code=201)
def create_worker(
    client_slug: str, payload: WorkerIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Worker:
    client = get_client_or_404(db, client_slug)
    code = payload.employee_number or payload.display_code or next_platform_code(db, client.id)
    worker = Worker(
        client_id=client.id,
        employee_number=code,
        display_code=payload.display_code or code,
        source="platform",
        full_name=payload.full_name,
        area=payload.area,
        phone=payload.phone,
        mobile=payload.mobile,
        social=payload.social,
        bank=payload.bank,
        account_number=payload.account_number,
        clabe=payload.clabe,
        ine_filename=payload.ine_filename,
    )
    db.add(worker)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Numero de empleado duplicado para el cliente") from exc
    db.refresh(worker)
    return worker


@app.put("/api/clients/{client_slug}/workers/{worker_id}", response_model=WorkerOut)
def update_worker(
    client_slug: str, worker_id: int, payload: WorkerIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Worker:
    client = get_client_or_404(db, client_slug)
    worker = db.query(Worker).filter(Worker.id == worker_id, Worker.client_id == client.id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Jornal no encontrado")
    if worker.source == "singa":
        raise HTTPException(status_code=403, detail="Los jornales SINGA no son editables")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "employee_number" and value is None:
            continue
        setattr(worker, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Numero de empleado duplicado para el cliente") from exc
    db.refresh(worker)
    return worker


@app.delete("/api/clients/{client_slug}/workers/{worker_id}", status_code=204)
def delete_worker(client_slug: str, worker_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    client = get_client_or_404(db, client_slug)
    worker = db.query(Worker).filter(Worker.id == worker_id, Worker.client_id == client.id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Jornal no encontrado")
    if worker.source == "singa":
        raise HTTPException(status_code=403, detail="Los jornales SINGA no se eliminan desde la plataforma")
    db.delete(worker)
    db.commit()
    return Response(status_code=204)


@app.get("/api/clients/{client_slug}/events", response_model=list[EventOut])
def list_events(client_slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Event]:
    client = get_client_or_404(db, client_slug)
    return db.query(Event).filter(Event.client_id == client.id).order_by(Event.event_date.desc(), Event.id.desc()).all()


@app.post("/api/clients/{client_slug}/events", response_model=EventOut, status_code=201)
def create_event(client_slug: str, payload: EventIn, _: User = Depends(current_user), db: Session = Depends(get_db)) -> Event:
    client = get_client_or_404(db, client_slug)
    event = Event(client_id=client.id, **payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@app.delete("/api/clients/{client_slug}/events/{event_id}", status_code=204)
def delete_event(client_slug: str, event_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    client = get_client_or_404(db, client_slug)
    event = db.query(Event).filter(Event.id == event_id, Event.client_id == client.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    db.delete(event)
    db.commit()
    return Response(status_code=204)


@app.get("/api/clients/{client_slug}/events/{event_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    client_slug: str, event_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[ShiftAssignment]:
    client = get_client_or_404(db, client_slug)
    event = db.query(Event).filter(Event.id == event_id, Event.client_id == client.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return (
        db.query(ShiftAssignment)
        .options(joinedload(ShiftAssignment.worker))
        .filter(ShiftAssignment.event_id == event.id)
        .order_by(ShiftAssignment.shift, ShiftAssignment.id)
        .all()
    )


@app.post("/api/clients/{client_slug}/events/{event_id}/assignments", response_model=AssignmentOut, status_code=201)
def create_assignment(
    client_slug: str,
    event_id: int,
    payload: AssignmentIn,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ShiftAssignment:
    client = get_client_or_404(db, client_slug)
    event = db.query(Event).filter(Event.id == event_id, Event.client_id == client.id).first()
    singa_client = db.query(Client).filter(Client.slug == "singa", Client.active.is_(True)).first()
    allowed_client_ids = [client.id]
    if singa_client:
        allowed_client_ids.append(singa_client.id)
    worker = db.query(Worker).filter(Worker.id == payload.worker_id, Worker.client_id.in_(allowed_client_ids)).first()
    if not event or not worker:
        raise HTTPException(status_code=404, detail="Evento o jornal no encontrado")
    assignment = ShiftAssignment(
        event_id=event.id,
        worker_id=worker.id,
        shift=payload.shift,
        worker_role=payload.worker_role,
        pay_amount=salary_for_shift(event, payload.shift, payload.worker_role),
    )
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El jornal ya esta asignado a ese turno") from exc
    db.refresh(assignment)
    return assignment


@app.delete("/api/clients/{client_slug}/assignments/{assignment_id}", status_code=204)
def delete_assignment(
    client_slug: str, assignment_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    client = get_client_or_404(db, client_slug)
    assignment = (
        db.query(ShiftAssignment)
        .join(Event)
        .filter(ShiftAssignment.id == assignment_id, Event.client_id == client.id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Asignacion no encontrada")
    db.delete(assignment)
    db.commit()
    return Response(status_code=204)


@app.get("/api/clients/{client_slug}/summary", response_model=SummaryOut)
def summary(client_slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)) -> SummaryOut:
    client = get_client_or_404(db, client_slug)
    before_count = func.coalesce(func.sum(case((ShiftAssignment.shift == "before", 1), else_=0)), 0)
    during_count = func.coalesce(func.sum(case((ShiftAssignment.shift == "during", 1), else_=0)), 0)
    after_count = func.coalesce(func.sum(case((ShiftAssignment.shift == "after", 1), else_=0)), 0)
    supervisor_shift_count = func.coalesce(func.sum(case((ShiftAssignment.worker_role == "supervisor", 1), else_=0)), 0)
    rows = (
        db.query(
            Worker.id,
            Worker.full_name,
            Worker.area,
            before_count.label("before_count"),
            during_count.label("during_count"),
            after_count.label("after_count"),
            supervisor_shift_count.label("supervisor_shift_count"),
            func.count(ShiftAssignment.id).label("shift_count"),
            func.coalesce(func.sum(ShiftAssignment.pay_amount), 0).label("total_pay"),
        )
        .join(ShiftAssignment, ShiftAssignment.worker_id == Worker.id)
        .filter(Worker.client_id == client.id)
        .group_by(Worker.id)
        .order_by(func.coalesce(func.sum(ShiftAssignment.pay_amount), 0).desc())
        .all()
    )
    event_count = db.query(func.count(Event.id)).filter(Event.client_id == client.id).scalar() or 0
    total_shifts = sum(row.shift_count for row in rows)
    total_pay = sum((row.total_pay for row in rows), Decimal("0"))
    return SummaryOut(
        events=event_count,
        active_workers=len(rows),
        total_shifts=total_shifts,
        total_pay=total_pay,
        rows=[
            SummaryRow(
                worker_id=row.id,
                full_name=row.full_name,
                area=row.area,
                before_count=row.before_count,
                during_count=row.during_count,
                after_count=row.after_count,
                shift_count=row.shift_count,
                supervisor_shift_count=row.supervisor_shift_count,
                total_pay=row.total_pay,
            )
            for row in rows
        ],
    )
