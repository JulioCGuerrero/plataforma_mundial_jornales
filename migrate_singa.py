import os
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row
import pyodbc


SQLSERVER_SERVER = os.getenv("SINGA_SQLSERVER_SERVER", "192.168.12.3")
SQLSERVER_DATABASE = os.getenv("SINGA_SQLSERVER_DATABASE", "SINGA")
SQLSERVER_USERNAME = os.getenv("SINGA_SQLSERVER_USERNAME", "alpreb")
SQLSERVER_PASSWORD = os.getenv("SINGA_SQLSERVER_PASSWORD", "s1st3m4s@_")
SQLSERVER_DRIVER = os.getenv("SINGA_SQLSERVER_DRIVER", "ODBC Driver 17 for SQL Server")
SQLSERVER_TIMEOUT = int(os.getenv("SINGA_SQLSERVER_TIMEOUT", "10"))
SQLSERVER_ENCRYPT = os.getenv("SINGA_SQLSERVER_ENCRYPT", "no")

POSTGRES_DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
CLIENT_SLUG = os.getenv("CLIENT_SLUG", "singa").strip() or "singa"


@dataclass
class SingaWorker:
    external_id: str
    full_name: str
    bank: str | None
    account_number: str | None
    clabe: str | None
    phone: str | None
    emergency_phone: str | None
    emergency_contact: str | None


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


def detect_bank_column(cursor: pyodbc.Cursor) -> str:
    print("[2/5] Detectando columna de nombre en tb_banco...", flush=True)
    candidates = ["banco", "nombre", "descripcion", "des_banco", "nb_banco"]
    rows = cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'tb_banco'
        """
    ).fetchall()
    columns = {row[0].lower(): row[0] for row in rows}
    for candidate in candidates:
        if candidate in columns:
            print(f"[2/5] Columna banco detectada: {columns[candidate]}", flush=True)
            return columns[candidate]
    raise RuntimeError(f"No encontre columna de nombre en tb_banco. Columnas: {sorted(columns.values())}")


def fetch_singa_workers() -> list[SingaWorker]:
    with sqlserver_connection() as conn:
        cursor = conn.cursor()
        bank_column = detect_bank_column(cursor)
        print("[3/5] Leyendo tb_jornalero con id_status = 1...", flush=True)
        query = f"""
            SELECT
                j.id_jornalero,
                LTRIM(RTRIM(CONCAT(
                    COALESCE(j.nombre, ''), ' ',
                    COALESCE(j.paterno, ''), ' ',
                    COALESCE(j.materno, '')
                ))) AS nombre_completo,
                b.{bank_column} AS banco,
                j.cuenta,
                j.clabe,
                j.telefono,
                j.telefono_emergencia,
                j.contacto_emergencia
            FROM tb_jornalero j
            LEFT JOIN tb_banco b ON b.id_banco = j.id_banco
            WHERE j.id_status = 1
            ORDER BY j.id_jornalero
        """
        rows = cursor.execute(query).fetchall()
        workers = [
            SingaWorker(
                external_id=str(row.id_jornalero),
                full_name=str(row.nombre_completo).strip(),
                bank=clean_text(row.banco, 120),
                account_number=clean_text(row.cuenta, 80),
                clabe=clean_text(row.clabe, 18),
                phone=clean_text(row.telefono, 40),
                emergency_phone=clean_text(row.telefono_emergencia, 40),
                emergency_contact=clean_text(row.contacto_emergencia, 120),
            )
            for row in rows
            if str(row.nombre_completo).strip()
        ]
        print(f"[3/5] Jornales SINGA encontrados: {len(workers)}", flush=True)
        return workers


def client_ids(conn: psycopg.Connection) -> list[int]:
    print(f"[4/5] Preparando cliente central destino: {CLIENT_SLUG}", flush=True)
    row = conn.execute("SELECT id FROM clients WHERE slug = %s", (CLIENT_SLUG,)).fetchone()
    if row:
        conn.execute("UPDATE clients SET active = true, updated_at = now() WHERE id = %s", (row[0],))
        client_id = row[0]
    else:
        row = conn.execute(
            """
            INSERT INTO clients (slug, name, subtitle, active)
            VALUES (%s, 'SINGA', 'Jornales importados desde SINGA', true)
            RETURNING id
            """,
            (CLIENT_SLUG,),
        ).fetchone()
        client_id = row[0]
    print(f"[4/5] Cliente central ID: {client_id}", flush=True)
    return [client_id]


def upsert_workers(workers: list[SingaWorker]) -> None:
    print("[4/5] Conectando a PostgreSQL destino...", flush=True)
    with psycopg.connect(POSTGRES_DSN) as conn:
        print("[4/5] Conexion PostgreSQL OK.", flush=True)
        clients = client_ids(conn)
        client_id = clients[0]
        with conn.cursor(row_factory=dict_row) as cur:
            print("[5/5] Revisando jornales ya existentes en Cloud SQL...", flush=True)
            cur.execute("SELECT employee_number FROM workers WHERE client_id = %s", (client_id,))
            existing_numbers = {str(row["employee_number"]) for row in cur.fetchall()}

        missing_workers = [worker for worker in workers if worker.external_id not in existing_numbers]
        skipped = len(workers) - len(missing_workers)
        print(f"[5/5] Faltantes detectados: {len(missing_workers)}. Ya existian sin cambios: {skipped}.", flush=True)
        if not missing_workers:
            conn.commit()
            print("[5/5] No habia jornales nuevos por insertar.", flush=True)
            return

        insert_sql = """
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
            VALUES (%s, %s, %s, 'singa', %s, 'jornal', %s, 'SINGA', %s, %s, %s, %s, %s, %s, true)
            ON CONFLICT (client_id, employee_number) DO NOTHING
        """
        inserted = 0
        batch_size = int(os.getenv("SINGA_INSERT_BATCH_SIZE", "250"))
        with conn.cursor() as cur:
            for start in range(0, len(missing_workers), batch_size):
                batch = missing_workers[start : start + batch_size]
                cur.executemany(
                    insert_sql,
                    [
                        (
                            client_id,
                            worker.external_id,
                            worker.external_id,
                            worker.external_id,
                            worker.full_name,
                            worker.phone,
                            worker.emergency_phone,
                            worker.emergency_contact,
                            worker.bank,
                            worker.account_number,
                            worker.clabe,
                        )
                        for worker in batch
                    ],
                )
                conn.commit()
                inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(batch)
                print(f"[5/5] Insertados {min(start + len(batch), len(missing_workers))}/{len(missing_workers)} faltantes", flush=True)
        print(f"[5/5] Commit OK. Nuevos: {inserted}. Ya existian sin cambios: {skipped}.", flush=True)


def main() -> None:
    print("Iniciando migracion SINGA -> PostgreSQL", flush=True)
    workers = fetch_singa_workers()
    upsert_workers(workers)
    target = CLIENT_SLUG or "todos los clientes"
    print(f"Revision de faltantes SINGA terminada en {target}. Registros origen revisados: {len(workers)}.")


if __name__ == "__main__":
    main()
