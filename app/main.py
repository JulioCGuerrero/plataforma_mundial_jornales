import base64
import csv
import html
import io
import posixpath
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import Application, Client, Event, PayrollPeriod, ShiftAssignment, User, Worker
from app.schemas import (
    ApplicationIn,
    ApplicationOut,
    ApplicationStatusIn,
    PublicApplyOut,
    PublicEventOut,
    AssignmentIn,
    AssignmentOut,
    ClientOut,
    EventIn,
    EventOut,
    LoginIn,
    PayrollPeriodIn,
    PayrollPeriodOut,
    SummaryOut,
    SummaryRow,
    TokenOut,
    WorkerIn,
    WorkerImportIn,
    WorkerImportOut,
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


@app.on_event("startup")
def ensure_worker_columns() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS payroll_periods (
            id SERIAL PRIMARY KEY,
            source_id VARCHAR(80) UNIQUE,
            period_code VARCHAR(80),
            period_type VARCHAR(40),
            name VARCHAR(180) NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            year INTEGER NOT NULL,
            active BOOLEAN DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_year ON payroll_periods (year)",
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_start_date ON payroll_periods (start_date)",
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_end_date ON payroll_periods (end_date)",
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_period_code ON payroll_periods (period_code)",
        "ALTER TABLE payroll_periods ADD COLUMN IF NOT EXISTS period_type VARCHAR(40)",
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_period_type ON payroll_periods (period_type)",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS edited BOOLEAN DEFAULT false",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS vetoed BOOLEAN DEFAULT false",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS veto_date DATE",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS veto_reason TEXT",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS veto_cleared_date DATE",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS veto_cleared_reason TEXT",
        "ALTER TABLE workers ALTER COLUMN clabe TYPE VARCHAR(80)",
        """
        CREATE TABLE IF NOT EXISTS applications (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            token VARCHAR(80) NOT NULL UNIQUE,
            full_name VARCHAR(180) NOT NULL,
            phone VARCHAR(40) NOT NULL,
            email VARCHAR(255),
            area VARCHAR(120),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_applications_event_id ON applications (event_id)",
        "CREATE INDEX IF NOT EXISTS ix_applications_token ON applications (token)",
        "CREATE INDEX IF NOT EXISTS ix_applications_status ON applications (status)",
        "ALTER TABLE applications ADD COLUMN IF NOT EXISTS ine_filename VARCHAR(255)",
        "ALTER TABLE applications ADD COLUMN IF NOT EXISTS ine_storage_path VARCHAR(500)",
    ]
    with SessionLocal() as db:
        for statement in statements:
            db.execute(text(statement))
        db.commit()


def get_client_or_404(db: Session, client_slug: str) -> Client:
    client = db.query(Client).filter(Client.slug == client_slug, Client.active.is_(True)).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return client


def get_period_or_404(db: Session, period_id: int) -> PayrollPeriod:
    period = db.query(PayrollPeriod).filter(PayrollPeriod.id == period_id, PayrollPeriod.active.is_(True)).first()
    if not period:
        raise HTTPException(status_code=404, detail="Periodo de nomina no encontrado")
    return period


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


WORKER_IMPORT_HEADERS = [
    "Numero",
    "Nombre",
    "Area",
    "Tipo",
    "Telefono",
    "Telefono 2",
    "Contacto/Redes",
    "Banco",
    "Cuenta",
    "CLABE",
    "INE",
    "Veto",
    "Fecha veto",
    "Motivo veto",
    "Fecha desmarque",
    "Motivo desmarque",
]


class TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self.current_row = []
        elif tag.lower() in {"td", "th"} and self.current_row is not None:
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.current_row is not None and self.current_cell is not None:
            self.current_row.append(html.unescape("".join(self.current_cell)).strip())
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None


def cell_text(value: object) -> str:
    text_value = str(value or "").strip()
    if text_value.endswith(".0") and text_value[:-2].isdigit():
        return text_value[:-2]
    return text_value


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"si", "sí", "s", "yes", "true", "1", "x"}


def parse_import_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    try:
        serial = int(float(value))
    except ValueError:
        return None
    if serial <= 0:
        return None
    return date(1899, 12, 30) + timedelta(days=serial)


def parse_csv_rows(content: bytes) -> list[list[str]]:
    text_content = content.decode("utf-8-sig", errors="replace")
    return [[cell_text(cell) for cell in row] for row in csv.reader(io.StringIO(text_content))]


def parse_html_table_rows(content: bytes) -> list[list[str]]:
    parser = TableHTMLParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    return parser.rows


def parse_xlsx_sheet_rows(workbook: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[str]]:
    namespaces = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    }
    sheet_root = ElementTree.fromstring(workbook.read(sheet_path))
    rows: list[list[str]] = []
    for row in sheet_root.findall(".//main:sheetData/main:row", namespaces):
        values_by_col: dict[int, str] = {}
        for cell in row.findall("main:c", namespaces):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            col_idx = 0
            if match:
                for char in match.group(1):
                    col_idx = col_idx * 26 + ord(char) - ord("A") + 1
                col_idx -= 1
            cell_type = cell.attrib.get("t")
            inline_text = cell.find("main:is/main:t", namespaces)
            raw_value = cell.findtext("main:v", default="", namespaces=namespaces)
            if cell_type == "s" and raw_value:
                try:
                    value = shared_strings[int(raw_value)]
                except (IndexError, ValueError):
                    value = ""
            elif cell_type == "inlineStr" and inline_text is not None:
                value = inline_text.text or ""
            else:
                value = raw_value
            values_by_col[col_idx] = cell_text(value)
        if values_by_col:
            rows.append([values_by_col.get(index, "") for index in range(max(values_by_col) + 1)])
    return rows


def parse_xlsx_rows(content: bytes) -> list[list[str]]:
    namespaces = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("main:si", namespaces):
                parts = [node.text or "" for node in item.findall(".//main:t", namespaces)]
                shared_strings.append("".join(parts))

        workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        sheets = workbook_root.findall("main:sheets/main:sheet", namespaces)
        if not sheets:
            return []
        rels_root = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        targets_by_id: dict[str, str] = {}
        for rel in rels_root.findall("rel:Relationship", namespaces):
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                targets_by_id[rel_id] = target

        relationship_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        preferred = sorted(
            sheets,
            key=lambda sheet: 0
            if any(word in sheet.attrib.get("name", "").lower() for word in ("layout", "import"))
            else 1,
        )
        fallback_rows: list[list[str]] = []
        for sheet in preferred:
            target = targets_by_id.get(sheet.attrib.get(relationship_key, ""))
            if not target:
                continue
            if target.startswith("/"):
                sheet_path = target.lstrip("/")
            else:
                sheet_path = posixpath.normpath(posixpath.join("xl", target))
            rows = parse_xlsx_sheet_rows(workbook, sheet_path, shared_strings)
            if not fallback_rows:
                fallback_rows = rows
            if rows:
                header = [cell_text(cell) for cell in rows[0][: len(WORKER_IMPORT_HEADERS)]]
                if header == WORKER_IMPORT_HEADERS and len(rows[0]) >= len(WORKER_IMPORT_HEADERS):
                    return rows
        return fallback_rows


def parse_worker_import_rows(filename: str, content: bytes) -> list[list[str]]:
    lower_name = filename.lower()
    if lower_name.endswith(".xlsx"):
        return parse_xlsx_rows(content)
    if lower_name.endswith(".xls") or content.lstrip().lower().startswith(b"<html"):
        return parse_html_table_rows(content)
    if lower_name.endswith(".csv"):
        return parse_csv_rows(content)
    raise HTTPException(status_code=400, detail="Usa un archivo .xlsx, .xls o .csv con el layout de jornales")


def normalize_worker_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"supervisor", "supervision", "supervisión"}:
        return "supervisor"
    return "jornal"


NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/api/public/events/{event_id}", response_model=PublicEventOut)
def public_event_info(event_id: int, db: Session = Depends(get_db)) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return event


@app.post("/api/public/events/{event_id}/apply", response_model=PublicApplyOut, status_code=201)
async def public_apply(
    event_id: int,
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(None),
    area: str = Form(None),
    ine: UploadFile = File(None),
    db: Session = Depends(get_db),
) -> Application:
    import os
    import secrets as _secrets
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    app_token = _secrets.token_urlsafe(20)
    ine_filename = None
    ine_storage_path = None
    if ine and ine.filename:
        ext = os.path.splitext(ine.filename)[1].lower() or ".jpg"
        ine_filename = f"{app_token}{ext}"
        upload_dir = STATIC_DIR / "uploads" / "ine"
        upload_dir.mkdir(parents=True, exist_ok=True)
        content = await ine.read()
        with open(upload_dir / ine_filename, "wb") as f:
            f.write(content)
        ine_storage_path = f"/static/uploads/ine/{ine_filename}"
    application = Application(
        event_id=event_id,
        token=app_token,
        full_name=full_name.strip(),
        phone=phone.strip(),
        email=email.strip() if email else None,
        area=area.strip() if area else None,
        ine_filename=ine_filename,
        ine_storage_path=ine_storage_path,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


@app.get("/apply/{event_id}", include_in_schema=False)
def apply_page(event_id: int) -> FileResponse:
    return FileResponse(STATIC_DIR / "apply.html", headers=NO_CACHE)


@app.get("/", include_in_schema=False)
def portal() -> FileResponse:
    return FileResponse(STATIC_DIR / "portal.html", headers=NO_CACHE)


@app.get("/operaciones", include_in_schema=False)
def operaciones() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)


@app.get("/reclut", include_in_schema=False)
def reclut() -> FileResponse:
    return FileResponse(STATIC_DIR / "reclut.html", headers=NO_CACHE)


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


@app.get("/api/payroll-periods", response_model=list[PayrollPeriodOut])
def list_payroll_periods(
    year: int = 2026, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[PayrollPeriod]:
    return (
        db.query(PayrollPeriod)
        .filter(PayrollPeriod.year == year, PayrollPeriod.active.is_(True))
        .order_by(PayrollPeriod.start_date, PayrollPeriod.id)
        .all()
    )


@app.post("/api/payroll-periods", response_model=PayrollPeriodOut)
def create_payroll_period(
    payload: PayrollPeriodIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PayrollPeriod:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="La fecha fin no puede ser menor que la fecha inicio")
    source_id = f"manual-{payload.year}-{payload.period_type}-{payload.start_date.isoformat()}"
    existing = db.query(PayrollPeriod).filter(PayrollPeriod.source_id == source_id).first()
    if existing and existing.active:
        raise HTTPException(status_code=409, detail="Ya existe un periodo manual con esa fecha de inicio")
    period = existing or PayrollPeriod(source_id=source_id)
    period.period_code = payload.period_code or None
    period.period_type = payload.period_type
    period.name = payload.name
    period.start_date = payload.start_date
    period.end_date = payload.end_date
    period.year = payload.year
    period.active = True
    db.add(period)
    db.commit()
    db.refresh(period)
    return period


@app.put("/api/payroll-periods/{period_id}", response_model=PayrollPeriodOut)
def update_payroll_period(
    period_id: int, payload: PayrollPeriodIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PayrollPeriod:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="La fecha fin no puede ser menor que la fecha inicio")
    period = get_period_or_404(db, period_id)
    period.period_code = payload.period_code or None
    period.period_type = payload.period_type
    period.name = payload.name
    period.start_date = payload.start_date
    period.end_date = payload.end_date
    period.year = payload.year
    db.commit()
    db.refresh(period)
    return period


@app.delete("/api/payroll-periods/{period_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payroll_period(
    period_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    period = get_period_or_404(db, period_id)
    period.active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/reports/payroll-final.csv")
def payroll_final_report(
    period_id: int | None = None, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    period = get_period_or_404(db, period_id) if period_id else None
    query = (
        db.query(
            Client.name.label("client_name"),
            Event.name.label("event_name"),
            Event.event_date,
            Event.event_type,
            Worker.employee_number,
            Worker.display_code,
            Worker.full_name,
            Worker.area,
            Worker.worker_type,
            Worker.source,
            Worker.bank,
            Worker.account_number,
            Worker.clabe,
            ShiftAssignment.shift,
            ShiftAssignment.worker_role,
            ShiftAssignment.pay_amount,
        )
        .join(Event, Event.id == ShiftAssignment.event_id)
        .join(Client, Client.id == Event.client_id)
        .join(Worker, Worker.id == ShiftAssignment.worker_id)
        .filter(Client.active.is_(True))
        .order_by(Client.name, Event.event_date, Event.name, Worker.full_name)
    )
    if period:
        query = query.filter(Event.event_date >= period.start_date, Event.event_date <= period.end_date)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Periodo",
            "Fecha inicio",
            "Fecha fin",
            "Cliente",
            "Evento",
            "Fecha evento",
            "Tipo evento",
            "Numero",
            "Nombre",
            "Area",
            "Tipo persona",
            "Origen",
            "Banco",
            "Cuenta",
            "CLABE",
            "Horario",
            "Pago",
        ]
    )
    shift_labels = {"before": "Horario 1", "during": "Horario 2", "after": "Horario 3"}
    for row in query.all():
        writer.writerow(
            [
                period.name if period else "Todos",
                period.start_date.isoformat() if period else "",
                period.end_date.isoformat() if period else "",
                row.client_name,
                row.event_name,
                row.event_date.isoformat(),
                row.event_type,
                row.display_code or row.employee_number,
                row.full_name,
                row.area,
                "Supervisor" if row.worker_role == "supervisor" else "Jornal",
                row.source,
                row.bank or "",
                row.account_number or "",
                row.clabe or "",
                shift_labels.get(row.shift, row.shift),
                f"{Decimal(row.pay_amount or 0):.2f}",
            ]
        )
    filename_period = f"periodo_{period.id}" if period else "todos"
    headers = {"Content-Disposition": f'attachment; filename="nomina_final_{filename_period}.csv"'}
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


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
    provided_code = payload.employee_number or payload.display_code
    if payload.worker_type == "supervisor" and not provided_code:
        raise HTTPException(status_code=400, detail="El folio SINGA del supervisor es obligatorio")
    code = provided_code or next_platform_code(db, client.id)
    worker = Worker(
        client_id=client.id,
        employee_number=code,
        display_code=payload.display_code or code,
        source="platform",
        worker_type=payload.worker_type,
        full_name=payload.full_name,
        area=payload.area,
        phone=payload.phone,
        mobile=payload.mobile,
        social=payload.social,
        bank=payload.bank,
        account_number=payload.account_number,
        clabe=payload.clabe,
        ine_filename=payload.ine_filename,
        vetoed=payload.vetoed,
        veto_date=payload.veto_date,
        veto_reason=payload.veto_reason,
        veto_cleared_date=payload.veto_cleared_date,
        veto_cleared_reason=payload.veto_cleared_reason,
    )
    db.add(worker)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Numero de empleado duplicado para el cliente") from exc
    db.refresh(worker)
    return worker


@app.post("/api/clients/{client_slug}/workers/import", response_model=WorkerImportOut)
def import_workers(
    client_slug: str, payload: WorkerImportIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> WorkerImportOut:
    client = get_client_or_404(db, client_slug)
    try:
        content = base64.b64decode(payload.content_base64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Archivo invalido") from exc

    rows = [row for row in parse_worker_import_rows(payload.filename, content) if any(cell_text(cell) for cell in row)]
    if not rows:
        raise HTTPException(status_code=400, detail="El archivo esta vacio")

    header = [cell_text(cell) for cell in rows[0][: len(WORKER_IMPORT_HEADERS)]]
    if header != WORKER_IMPORT_HEADERS or len(rows[0]) < len(WORKER_IMPORT_HEADERS):
        expected = ", ".join(WORKER_IMPORT_HEADERS)
        raise HTTPException(status_code=400, detail=f"Layout invalido. Encabezados esperados: {expected}")

    result = WorkerImportOut()
    seen_numbers: set[str] = set()
    next_auto_index = int(next_platform_code(db, client.id).split("-", 1)[1])
    for index, row in enumerate(rows[1:], start=2):
        values = [cell_text(cell) for cell in row]
        values += [""] * (len(WORKER_IMPORT_HEADERS) - len(values))
        data = dict(zip(WORKER_IMPORT_HEADERS, values[: len(WORKER_IMPORT_HEADERS)]))
        if not data["Nombre"] and not data["Area"] and not data["Numero"]:
            result.skipped += 1
            continue
        if not data["Nombre"]:
            result.errors.append(f"Fila {index}: Nombre es obligatorio")
            continue
        if not data["Area"]:
            result.errors.append(f"Fila {index}: Area es obligatoria")
            continue
        worker_type = normalize_worker_type(data["Tipo"])
        if worker_type == "supervisor" and not data["Numero"]:
            result.errors.append(f"Fila {index}: Numero es obligatorio para supervisores")
            continue
        if data["Numero"] and data["Numero"] in seen_numbers:
            result.errors.append(f"Fila {index}: Numero duplicado dentro del archivo")
            continue
        if data["Numero"]:
            seen_numbers.add(data["Numero"])
        vetoed = parse_bool(data["Veto"])
        veto_date = parse_import_date(data["Fecha veto"])
        veto_cleared_date = parse_import_date(data["Fecha desmarque"])
        if vetoed and (not veto_date or not data["Motivo veto"]):
            result.errors.append(f"Fila {index}: si Veto es Si, captura Fecha veto y Motivo veto")
            continue

        if data["Numero"]:
            code = data["Numero"]
        else:
            code = f"fp-{next_auto_index:03d}"
            next_auto_index += 1
            while code in seen_numbers:
                code = f"fp-{next_auto_index:03d}"
                next_auto_index += 1
            seen_numbers.add(code)
        worker = db.query(Worker).filter(Worker.client_id == client.id, Worker.employee_number == code).first()
        is_new = worker is None
        if is_new:
            worker = Worker(client_id=client.id, employee_number=code, source="platform")
            db.add(worker)
        elif worker.source in {"singa", "ollamani"}:
            result.errors.append(f"Fila {index}: no se puede sobrescribir un jornal importado desde {worker.source}")
            continue

        worker.display_code = code
        worker.worker_type = worker_type
        worker.full_name = data["Nombre"]
        worker.area = data["Area"]
        worker.phone = data["Telefono"] or None
        worker.mobile = data["Telefono 2"] or None
        worker.social = data["Contacto/Redes"] or None
        worker.bank = data["Banco"] or None
        worker.account_number = data["Cuenta"] or None
        worker.clabe = data["CLABE"] or None
        worker.ine_filename = data["INE"] or None
        worker.vetoed = vetoed
        worker.veto_date = veto_date
        worker.veto_reason = data["Motivo veto"] or None
        worker.veto_cleared_date = veto_cleared_date
        worker.veto_cleared_reason = data["Motivo desmarque"] or None
        worker.edited = not is_new
        worker.edited_at = datetime.utcnow() if not is_new else None
        worker.active = True
        if is_new:
            result.created += 1
        else:
            result.updated += 1

    if result.errors:
        db.rollback()
        return result
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El archivo contiene numeros de empleado duplicados") from exc
    except DataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="El archivo contiene un valor demasiado largo") from exc
    return result


@app.put("/api/clients/{client_slug}/workers/{worker_id}", response_model=WorkerOut)
def update_worker(
    client_slug: str, worker_id: int, payload: WorkerIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Worker:
    client = get_client_or_404(db, client_slug)
    singa_client = db.query(Client).filter(Client.slug == "singa", Client.active.is_(True)).first()
    allowed_client_ids = [client.id]
    if singa_client:
        allowed_client_ids.append(singa_client.id)
    worker = db.query(Worker).filter(Worker.id == worker_id, Worker.client_id.in_(allowed_client_ids)).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Jornal no encontrado")
    if payload.worker_type == "supervisor" and not (payload.employee_number or payload.display_code):
        raise HTTPException(status_code=400, detail="El folio SINGA del supervisor es obligatorio")
    editable_keys = None
    if worker.source in {"singa", "ollamani"}:
        editable_keys = {
            "vetoed",
            "veto_date",
            "veto_reason",
            "veto_cleared_date",
            "veto_cleared_reason",
        }
    for key, value in payload.model_dump(exclude_unset=True).items():
        if editable_keys is not None and key not in editable_keys:
            continue
        if key in {"employee_number", "display_code"} and value is None:
            continue
        setattr(worker, key, value)
    worker.edited = True
    worker.edited_at = datetime.utcnow()
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
    if worker.source in {"singa", "ollamani"}:
        raise HTTPException(status_code=403, detail="Los jornales importados no se eliminan desde la plataforma")
    db.delete(worker)
    db.commit()
    return Response(status_code=204)


@app.get("/api/clients/{client_slug}/events", response_model=list[EventOut])
def list_events(
    client_slug: str,
    period_id: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Event]:
    client = get_client_or_404(db, client_slug)
    query = db.query(Event).filter(Event.client_id == client.id)
    if period_id:
        period = get_period_or_404(db, period_id)
        query = query.filter(Event.event_date >= period.start_date, Event.event_date <= period.end_date)
    return query.order_by(Event.event_date.desc(), Event.id.desc()).all()


@app.post("/api/clients/{client_slug}/events", response_model=EventOut, status_code=201)
def create_event(client_slug: str, payload: EventIn, _: User = Depends(current_user), db: Session = Depends(get_db)) -> Event:
    client = get_client_or_404(db, client_slug)
    event = Event(client_id=client.id, **payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@app.put("/api/clients/{client_slug}/events/{event_id}", response_model=EventOut)
def update_event(
    client_slug: str, event_id: int, payload: EventIn, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> Event:
    client = get_client_or_404(db, client_slug)
    event = db.query(Event).filter(Event.id == event_id, Event.client_id == client.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, key, value)
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
    if worker.vetoed:
        raise HTTPException(status_code=400, detail="El jornal esta vetado y no puede asignarse")
    if payload.worker_role == "supervisor" and worker.worker_type != "supervisor":
        raise HTTPException(status_code=400, detail="Selecciona un supervisor para listas de supervision")
    if payload.worker_role == "jornal" and worker.worker_type == "supervisor":
        raise HTTPException(status_code=400, detail="Selecciona un jornal para listas de jornales")
    existing = (
        db.query(ShiftAssignment)
        .filter(ShiftAssignment.event_id == event.id, ShiftAssignment.worker_id == worker.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="El jornal ya esta asignado en este evento")
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


@app.get("/api/clients/{client_slug}/events/{event_id}/applications", response_model=list[ApplicationOut])
def list_applications(
    client_slug: str, event_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Application]:
    client = get_client_or_404(db, client_slug)
    event = db.query(Event).filter(Event.id == event_id, Event.client_id == client.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return db.query(Application).filter(Application.event_id == event_id).order_by(Application.id.desc()).all()


@app.post("/api/clients/{client_slug}/events/{event_id}/applications", response_model=ApplicationOut, status_code=201)
def create_application(
    client_slug: str,
    event_id: int,
    body: ApplicationIn,
    db: Session = Depends(get_db),
) -> Application:
    import secrets
    event = db.query(Event).join(Client).filter(Event.id == event_id, Client.slug == client_slug).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    app_token = secrets.token_urlsafe(20)
    application = Application(
        event_id=event_id,
        token=app_token,
        full_name=body.full_name,
        phone=body.phone,
        email=body.email,
        area=body.area,
        notes=body.notes,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


@app.patch("/api/applications/{application_id}/status", response_model=ApplicationOut)
def update_application_status(
    application_id: int,
    body: ApplicationStatusIn,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Application:
    if body.status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=422, detail="Estado inválido")
    application = db.query(Application).filter(Application.id == application_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Postulante no encontrado")
    application.status = body.status
    if body.notes is not None:
        application.notes = body.notes
    db.commit()
    db.refresh(application)
    return application


@app.delete("/api/applications/{application_id}", status_code=204)
def delete_application(
    application_id: int,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    application = db.query(Application).filter(Application.id == application_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Postulante no encontrado")
    db.delete(application)
    db.commit()
    return Response(status_code=204)


@app.get("/api/clients/{client_slug}/summary", response_model=SummaryOut)
def summary(
    client_slug: str,
    period_id: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SummaryOut:
    client = get_client_or_404(db, client_slug)
    period = get_period_or_404(db, period_id) if period_id else None
    before_count = func.coalesce(func.sum(case((ShiftAssignment.shift == "before", 1), else_=0)), 0)
    during_count = func.coalesce(func.sum(case((ShiftAssignment.shift == "during", 1), else_=0)), 0)
    after_count = func.coalesce(func.sum(case((ShiftAssignment.shift == "after", 1), else_=0)), 0)
    supervisor_shift_count = func.coalesce(func.sum(case((ShiftAssignment.worker_role == "supervisor", 1), else_=0)), 0)
    rows_query = (
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
        .join(Event, Event.id == ShiftAssignment.event_id)
        .filter(Event.client_id == client.id)
    )
    event_count_query = db.query(func.count(Event.id)).filter(Event.client_id == client.id)
    if period:
        rows_query = rows_query.filter(Event.event_date >= period.start_date, Event.event_date <= period.end_date)
        event_count_query = event_count_query.filter(Event.event_date >= period.start_date, Event.event_date <= period.end_date)
    rows = rows_query.group_by(Worker.id).order_by(func.coalesce(func.sum(ShiftAssignment.pay_amount), 0).desc()).all()
    event_count = event_count_query.scalar() or 0
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
