import os
from dataclasses import dataclass
from datetime import date, datetime

import psycopg
import pyodbc


SQLSERVER_SERVER = os.getenv("SINGA_SQLSERVER_SERVER", "192.168.12.3")
SQLSERVER_DATABASE = os.getenv("SINGA_SQLSERVER_DATABASE", "SINGA")
SQLSERVER_USERNAME = os.getenv("SINGA_SQLSERVER_USERNAME", "alpreb")
SQLSERVER_PASSWORD = os.getenv("SINGA_SQLSERVER_PASSWORD", "s1st3m4s@_")
SQLSERVER_DRIVER = os.getenv("SINGA_SQLSERVER_DRIVER", "ODBC Driver 17 for SQL Server")
SQLSERVER_TIMEOUT = int(os.getenv("SINGA_SQLSERVER_TIMEOUT", "10"))
SQLSERVER_ENCRYPT = os.getenv("SINGA_SQLSERVER_ENCRYPT", "no")
PAYROLL_YEAR = int(os.getenv("PAYROLL_YEAR", "2026"))

POSTGRES_DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


@dataclass
class PayrollPeriodRow:
    source_id: str
    period_code: str | None
    period_type: str | None
    name: str
    start_date: date
    end_date: date


def sqlserver_connection() -> pyodbc.Connection:
    print(f"[1/4] Conectando a SQL Server {SQLSERVER_SERVER} / {SQLSERVER_DATABASE}...", flush=True)
    connection_string = (
        f"DRIVER={{{SQLSERVER_DRIVER}}};"
        f"SERVER={SQLSERVER_SERVER};"
        f"DATABASE={SQLSERVER_DATABASE};"
        f"UID={SQLSERVER_USERNAME};"
        f"PWD={SQLSERVER_PASSWORD};"
        f"Encrypt={SQLSERVER_ENCRYPT};"
        "TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(connection_string, timeout=SQLSERVER_TIMEOUT)
    print("[1/4] Conexion SQL Server OK.", flush=True)
    return conn


def clean_text(value: object, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length] if max_length else text


def as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def period_type_from_name(name: str | None) -> str | None:
    text = (name or "").strip().lower()
    if "quinc" in text:
        return "quincenal"
    if "seman" in text:
        return "semanal"
    return None


def pick_column(columns: dict[str, str], candidates: list[str], required: bool = True) -> str | None:
    normalized = {key.lower(): value for key, value in columns.items()}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    if required:
        raise RuntimeError(
            "No encontre columna requerida. Candidatas: "
            + ", ".join(candidates)
            + ". Columnas disponibles: "
            + ", ".join(columns.values())
        )
    return None


def table_columns(cursor: pyodbc.Cursor) -> dict[str, str]:
    rows = cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'tb_periodonomina'
        ORDER BY ORDINAL_POSITION
        """
    ).fetchall()
    columns = {row[0].lower(): row[0] for row in rows}
    if not columns:
        raise RuntimeError("No encontre la tabla tb_periodonomina en SQL Server")
    return columns


def fetch_periods() -> list[PayrollPeriodRow]:
    with sqlserver_connection() as conn:
        cursor = conn.cursor()
        columns = table_columns(cursor)
        id_col = pick_column(
            columns,
            ["id_periodo", "id_periodonomina", "id_periodo_nomina", "id"],
        )
        code_col = id_col
        name_col = pick_column(
            columns,
            ["descripcion", "nombre", "periodo", "nom_periodo", "des_periodo"],
            required=False,
        )
        year_col = pick_column(columns, ["anio", "ano", "year"], required=False)
        start_col = pick_column(
            columns,
            [
                "finicio",
                "fecha_inicio",
                "fechainicio",
                "inicio",
                "fecha_inicial",
                "fec_inicio",
                "f_inicio",
                "del",
                "desde",
            ],
        )
        end_col = pick_column(
            columns,
            ["ffin", "fecha_fin", "fechafin", "fin", "fecha_final", "fec_fin", "f_fin", "al", "hasta"],
        )

        select_cols = [start_col, end_col]
        for col in (id_col, code_col, name_col, year_col):
            if col and col not in select_cols:
                select_cols.append(col)
        where_parts = []
        params: list[object] = []
        if year_col:
            where_parts.append(f"[{year_col}] = ?")
            params.append(PAYROLL_YEAR)
        else:
            where_parts.append(f"(YEAR([{start_col}]) = ? OR YEAR([{end_col}]) = ?)")
            params.extend([PAYROLL_YEAR, PAYROLL_YEAR])
        query = f"""
            SELECT {", ".join(f"[{col}]" for col in select_cols)}
            FROM tb_periodonomina
            WHERE {" AND ".join(where_parts)}
            ORDER BY [{start_col}], [{end_col}]
        """
        print(f"[2/4] Leyendo tb_periodonomina para {PAYROLL_YEAR}...", flush=True)
        rows = cursor.execute(query, *params).fetchall()
        periods: list[PayrollPeriodRow] = []
        for index, row in enumerate(rows, start=1):
            values = dict(zip(select_cols, row))
            start_date = as_date(values[start_col])
            end_date = as_date(values[end_col])
            if not start_date or not end_date:
                continue
            if start_date.year != PAYROLL_YEAR and end_date.year != PAYROLL_YEAR:
                continue
            source_id = clean_text(values.get(id_col), 80) if id_col else None
            period_code = clean_text(values.get(code_col), 80) if code_col else None
            name = clean_text(values.get(name_col), 180) if name_col else None
            if not source_id:
                source_id = f"{PAYROLL_YEAR}-{index:03d}-{start_date.isoformat()}"
            if not name:
                label = period_code or str(index)
                name = f"Periodo {label} ({start_date:%d/%m/%Y} - {end_date:%d/%m/%Y})"
            periods.append(
                PayrollPeriodRow(
                    source_id=source_id,
                    period_code=period_code,
                    period_type=period_type_from_name(name),
                    name=name,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
        print(f"[2/4] Periodos encontrados: {len(periods)}", flush=True)
        return periods


def upsert_periods(periods: list[PayrollPeriodRow]) -> None:
    print("[3/4] Conectando a PostgreSQL destino...", flush=True)
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
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
                """
            )
            cur.execute("ALTER TABLE payroll_periods ADD COLUMN IF NOT EXISTS period_type VARCHAR(40)")
            cur.execute("UPDATE payroll_periods SET active = false, updated_at = now() WHERE year = %s", (PAYROLL_YEAR,))
            print(f"[4/4] Insertando/actualizando {len(periods)} periodos...", flush=True)
            for period in periods:
                cur.execute(
                    """
                    INSERT INTO payroll_periods (source_id, period_code, period_type, name, start_date, end_date, year, active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, true)
                    ON CONFLICT (source_id)
                    DO UPDATE SET
                        period_code = EXCLUDED.period_code,
                        period_type = EXCLUDED.period_type,
                        name = EXCLUDED.name,
                        start_date = EXCLUDED.start_date,
                        end_date = EXCLUDED.end_date,
                        year = EXCLUDED.year,
                        active = true,
                        updated_at = now()
                    """,
                    (
                        period.source_id,
                        period.period_code,
                        period.period_type,
                        period.name,
                        period.start_date,
                        period.end_date,
                        PAYROLL_YEAR,
                    ),
                )
        conn.commit()
    print("[4/4] Commit OK.", flush=True)


def main() -> None:
    periods = fetch_periods()
    upsert_periods(periods)
    print(f"Periodos {PAYROLL_YEAR} importados/actualizados: {len(periods)}")


if __name__ == "__main__":
    main()
