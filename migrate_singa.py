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

POSTGRES_DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
CLIENT_SLUG = os.getenv("CLIENT_SLUG", "singa").strip() or "singa"


@dataclass
class SingaWorker:
    external_id: str
    full_name: str
    bank: str | None
    account_number: str | None


def sqlserver_connection() -> pyodbc.Connection:
    print(f"[1/5] Conectando a SQL Server {SQLSERVER_SERVER} / {SQLSERVER_DATABASE}...", flush=True)
    connection_string = (
        f"DRIVER={{{SQLSERVER_DRIVER}}};"
        f"SERVER={SQLSERVER_SERVER};"
        f"DATABASE={SQLSERVER_DATABASE};"
        f"UID={SQLSERVER_USERNAME};"
        f"PWD={SQLSERVER_PASSWORD};"
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
                j.cuenta
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
                bank=str(row.banco).strip() if row.banco is not None else None,
                account_number=str(row.cuenta).strip() if row.cuenta is not None else None,
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
        with conn.cursor() as cur:
            total = len(workers)
            done = 0
            print(f"[5/5] Insertando/actualizando {total} registros...", flush=True)
            client_id = clients[0]
            for worker in workers:
                cur.execute(
                    """
                    INSERT INTO workers (
                        client_id,
                        employee_number,
                        display_code,
                        source,
                        external_id,
                        full_name,
                        area,
                        bank,
                        account_number,
                        active
                    )
                    VALUES (%s, %s, %s, 'singa', %s, %s, 'SINGA', %s, %s, true)
                    ON CONFLICT (client_id, employee_number)
                    DO UPDATE SET
                        display_code = EXCLUDED.display_code,
                        source = 'singa',
                        external_id = EXCLUDED.external_id,
                        full_name = EXCLUDED.full_name,
                        area = EXCLUDED.area,
                        bank = EXCLUDED.bank,
                        account_number = EXCLUDED.account_number,
                        active = true,
                        updated_at = now()
                    """,
                    (
                        client_id,
                        worker.external_id,
                        worker.external_id,
                        worker.external_id,
                        worker.full_name,
                        worker.bank,
                        worker.account_number,
                    ),
                )
                done += 1
                if done % 100 == 0:
                    print(f"[5/5] Progreso: {done}/{total}", flush=True)
        conn.commit()
        print(f"[5/5] Commit OK. Registros procesados: {total}", flush=True)


def main() -> None:
    print("Iniciando migracion SINGA -> PostgreSQL", flush=True)
    workers = fetch_singa_workers()
    upsert_workers(workers)
    target = CLIENT_SLUG or "todos los clientes"
    print(f"Importados/actualizados {len(workers)} jornales SINGA en {target}.")


if __name__ == "__main__":
    main()
