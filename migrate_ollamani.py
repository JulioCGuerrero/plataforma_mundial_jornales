import os
from dataclasses import dataclass

import psycopg
import pyodbc


SQLSERVER_SERVER = os.getenv("SINGA_SQLSERVER_SERVER", "192.168.2.3")
SQLSERVER_DATABASE = os.getenv("SINGA_SQLSERVER_DATABASE", "SINGA")
SQLSERVER_USERNAME = os.getenv("SINGA_SQLSERVER_USERNAME", "alpreb")
SQLSERVER_PASSWORD = os.getenv("SINGA_SQLSERVER_PASSWORD", "s1st3m4s@_")
SQLSERVER_DRIVER = os.getenv("SINGA_SQLSERVER_DRIVER", "ODBC Driver 17 for SQL Server")
SQLSERVER_TIMEOUT = int(os.getenv("SINGA_SQLSERVER_TIMEOUT", "10"))
SQLSERVER_ENCRYPT = os.getenv("SINGA_SQLSERVER_ENCRYPT", "no")

POSTGRES_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/jornales",
).replace("postgresql+psycopg://", "postgresql://")
CLIENT_SLUG = os.getenv("OLLAMANI_CLIENT_SLUG", "ollamani").strip() or "ollamani"
SOURCE = "ollamani"
SINGA_CLIENT_ID = int(os.getenv("OLLAMANI_SINGA_CLIENT_ID", "2401"))
SINGA_STATUS_ID = int(os.getenv("OLLAMANI_SINGA_STATUS_ID", "2"))


@dataclass
class OllamaniWorker:
    external_id: str
    full_name: str
    bank: str | None
    account_number: str | None
    clabe: str | None
    phone: str | None
    mobile: str | None
    social: str | None


def clean_text(value: object, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_length is not None:
        return text[:max_length]
    return text


def sqlserver_connection() -> pyodbc.Connection:
    print(f"[1/5] Conectando a SQL Server {SQLSERVER_SERVER} / {SQLSERVER_DATABASE}...", flush=True)
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
    print("[1/5] Conexion SQL Server OK.", flush=True)
    return conn


def table_columns(cursor: pyodbc.Cursor, table_name: str) -> dict[str, str]:
    rows = cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = ?
        """,
        table_name,
    ).fetchall()
    return {row[0].lower(): row[0] for row in rows}


def pick(columns: dict[str, str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate.lower() in columns:
            return columns[candidate.lower()]
    return None


def sql_expr(column: str | None, alias: str, default: str = "NULL") -> str:
    if column:
        return f"e.{column} AS {alias}"
    return f"{default} AS {alias}"


def fetch_ollamani_workers() -> list[OllamaniWorker]:
    with sqlserver_connection() as conn:
        cursor = conn.cursor()
        employee_columns = table_columns(cursor, "tb_empleado")
        bank_columns = table_columns(cursor, "tb_banco")

        id_column = pick(employee_columns, ["id_empleado", "idEmpleado", "id", "empleado"])
        if not id_column:
            raise RuntimeError(f"No encontre columna ID en tb_empleado. Columnas: {sorted(employee_columns.values())}")

        first_name = pick(employee_columns, ["nombre", "nombres", "name"])
        paternal = pick(employee_columns, ["paterno", "apellido_paterno", "ap_paterno"])
        maternal = pick(employee_columns, ["materno", "apellido_materno", "ap_materno"])
        full_name = pick(employee_columns, ["nombre_completo", "empleado", "descripcion"])
        if full_name:
            name_expr = f"LTRIM(RTRIM(e.{full_name})) AS nombre_completo"
        elif first_name:
            name_parts = [first_name]
            if paternal:
                name_parts.append(paternal)
            if maternal:
                name_parts.append(maternal)
            concat_parts: list[str] = []
            for index, column in enumerate(name_parts):
                if index:
                    concat_parts.append("' '")
                concat_parts.append(f"COALESCE(e.{column}, '')")
            name_expr = f"LTRIM(RTRIM(CONCAT({', '.join(concat_parts)}))) AS nombre_completo"
        else:
            raise RuntimeError(f"No encontre columnas de nombre en tb_empleado. Columnas: {sorted(employee_columns.values())}")

        account = pick(employee_columns, ["cuenta", "cuenta_bancaria", "no_cuenta", "num_cuenta"])
        clabe = pick(employee_columns, ["clabe", "clabe_interbancaria"])
        phone = pick(employee_columns, ["telefono", "tel", "telefono1"])
        mobile = pick(employee_columns, ["celular", "telefono_celular", "movil", "telefono2"])
        social = pick(employee_columns, ["contacto_emergencia", "email", "correo"])
        bank_id = pick(employee_columns, ["id_banco", "banco_id"])
        bank_name = pick(bank_columns, ["banco", "nombre", "descripcion", "des_banco", "nb_banco"])
        join_bank = f"LEFT JOIN tb_banco b ON b.id_banco = e.{bank_id}" if bank_id and bank_name else ""
        bank_expr = f"b.{bank_name} AS banco" if join_bank else "NULL AS banco"

        print(
            f"[2/5] Leyendo tb_empleado id_cliente={SINGA_CLIENT_ID}, id_status={SINGA_STATUS_ID}...",
            flush=True,
        )
        query = f"""
            SELECT
                e.{id_column} AS external_id,
                {name_expr},
                {bank_expr},
                {sql_expr(account, "cuenta")},
                {sql_expr(clabe, "clabe")},
                {sql_expr(phone, "telefono")},
                {sql_expr(mobile, "celular")},
                {sql_expr(social, "social")}
            FROM tb_empleado e
            {join_bank}
            WHERE e.id_cliente = ? AND e.id_status = ?
            ORDER BY e.{id_column}
        """
        rows = cursor.execute(query, SINGA_CLIENT_ID, SINGA_STATUS_ID).fetchall()
        workers = [
            OllamaniWorker(
                external_id=str(row.external_id),
                full_name=str(row.nombre_completo).strip(),
                bank=clean_text(row.banco, 120),
                account_number=clean_text(row.cuenta, 80),
                clabe=clean_text(row.clabe, 18),
                phone=clean_text(row.telefono, 40),
                mobile=clean_text(row.celular, 40),
                social=clean_text(row.social, 120),
            )
            for row in rows
            if str(row.nombre_completo).strip()
        ]
        print(f"[3/5] Empleados Ollamani encontrados: {len(workers)}", flush=True)
        return workers


def client_id(conn: psycopg.Connection) -> int:
    print(f"[4/5] Preparando cliente destino: {CLIENT_SLUG}", flush=True)
    row = conn.execute("SELECT id FROM clients WHERE slug = %s", (CLIENT_SLUG,)).fetchone()
    if row:
        conn.execute("UPDATE clients SET active = true, updated_at = now() WHERE id = %s", (row[0],))
        return row[0]
    row = conn.execute(
        """
        INSERT INTO clients (slug, name, subtitle, active)
        VALUES (%s, 'Ollamani', 'Empleados importados desde SINGA', true)
        RETURNING id
        """,
        (CLIENT_SLUG,),
    ).fetchone()
    return row[0]


def upsert_workers(workers: list[OllamaniWorker]) -> None:
    print("[4/5] Conectando a PostgreSQL destino...", flush=True)
    with psycopg.connect(POSTGRES_DSN) as conn:
        print("[4/5] Conexion PostgreSQL OK.", flush=True)
        target_client_id = client_id(conn)
        with conn.cursor() as cur:
            total = len(workers)
            print(f"[5/5] Insertando/actualizando {total} empleados Ollamani...", flush=True)
            for done, worker in enumerate(workers, start=1):
                cur.execute(
                    """
                    INSERT INTO workers (
                        client_id,
                        employee_number,
                        display_code,
                        source,
                        external_id,
                        worker_type,
                        full_name,
                        area,
                        phone,
                        mobile,
                        social,
                        bank,
                        account_number,
                        clabe,
                        active
                    )
                    VALUES (%s, %s, %s, %s, %s, 'jornal', %s, 'Ollamani', %s, %s, %s, %s, %s, %s, true)
                    ON CONFLICT (client_id, employee_number)
                    DO UPDATE SET
                        display_code = EXCLUDED.display_code,
                        source = EXCLUDED.source,
                        external_id = EXCLUDED.external_id,
                        worker_type = 'jornal',
                        full_name = EXCLUDED.full_name,
                        area = EXCLUDED.area,
                        phone = EXCLUDED.phone,
                        mobile = EXCLUDED.mobile,
                        social = EXCLUDED.social,
                        bank = EXCLUDED.bank,
                        account_number = EXCLUDED.account_number,
                        clabe = EXCLUDED.clabe,
                        active = true,
                        updated_at = now()
                    """,
                    (
                        target_client_id,
                        worker.external_id,
                        worker.external_id,
                        SOURCE,
                        worker.external_id,
                        worker.full_name,
                        worker.phone,
                        worker.mobile,
                        worker.social,
                        worker.bank,
                        worker.account_number,
                        worker.clabe,
                    ),
                )
                if done % 100 == 0:
                    print(f"[5/5] Progreso: {done}/{total}", flush=True)
        conn.commit()
        print(f"[5/5] Commit OK. Registros procesados: {len(workers)}", flush=True)


def main() -> None:
    print("Iniciando migracion Ollamani tb_empleado -> PostgreSQL", flush=True)
    workers = fetch_ollamani_workers()
    upsert_workers(workers)
    print(f"Importados/actualizados {len(workers)} empleados Ollamani en {CLIENT_SLUG}.")


if __name__ == "__main__":
    main()
