"""
seed_demo.py — Crea datos de demostración (eventos + postulantes) en la BD local.
Corre con el Cloud SQL Proxy activo (127.0.0.1:15432).

Uso:
    .\.venv\Scripts\python.exe seed_demo.py
    o con el mismo entorno que run_local.ps1:
    python seed_demo.py
"""
import os
import random
import secrets
import string
from datetime import date, timedelta

import psycopg

DSN = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:Alpreb123batia+@127.0.0.1:15432/postgres",
).replace("postgresql+psycopg://", "postgresql://")

# ── Datos realistas ─────────────────────────────────────────────────────────

NOMBRES = [
    "María García López", "José Antonio Ramírez", "Ana Martínez Torres",
    "Carlos Rodríguez Sánchez", "Laura Hernández Pérez", "Pedro Soto Morales",
    "Sofía Ramírez Cruz", "Diego Torres Vega", "Fernanda López Díaz",
    "Miguel Ángel Reyes", "Valeria Moreno Ruiz", "Alejandro Jiménez Castillo",
    "Daniela Flores Méndez", "Roberto Alvarez Ortiz", "Gabriela Muñoz Vargas",
    "Luis Eduardo Peña", "Mariana Delgado Ríos", "Héctor Gutiérrez Luna",
    "Paola Medina Aguilar", "Andrés Castro Herrera", "Lucía Navarro Ibáñez",
    "Javier Fuentes Mora", "Sandra Ramos Espinoza", "Ernesto Vázquez Núñez",
    "Claudia Paredes Salinas", "Francisco Mendoza Trejo", "Beatriz Orozco Cano",
    "Salvador Lara Padilla", "Yolanda Carrillo Estrada", "Enrique Alvarado Ponce",
    "Cristina Montes Guerrero", "Arturo Contreras Bravo", "Irene Figueroa Acosta",
    "Raúl Cervantes Domínguez", "Lorena Sandoval Téllez",
]

TELEFONOS = [
    "55 1234 5678", "55 8765 4321", "55 4321 8765", "55 9876 1234",
    "56 1111 2222", "56 3333 4444", "55 5678 9012", "55 2222 3333",
    "56 6666 7777", "55 4444 5555", "56 8888 9999", "55 0000 1111",
    "56 2345 6789", "55 9012 3456", "56 7890 1234",
]

AREAS = ["Seguridad", "Acomodadores", "Limpieza", "Taquillas", "Logística",
          "Estacionamiento", "Control de acceso", "Staff general", "Supervisión"]

STATUSES = ["pending", "pending", "pending", "approved", "approved", "rejected"]

EVENTOS_DEMO = [
    {
        "name": "Concierto Coldplay — Music of the Spheres",
        "event_type": "Concierto",
        "days_offset": 14,
        "salary_during": 580,
        "operator_positions": 320,
        "supervisor_positions": 28,
        "description": "Producción internacional. Personal bilingüe de preferencia. Uniforme negro obligatorio.",
    },
    {
        "name": "Clásico Nacional — América vs Chivas",
        "event_type": "Fútbol",
        "days_offset": 7,
        "salary_during": 480,
        "operator_positions": 240,
        "supervisor_positions": 20,
        "description": "Partido de alta afluencia. Protocolo especial de seguridad en accesos.",
    },
    {
        "name": "Gran Premio de México — F1 2026",
        "event_type": "Automovilismo",
        "days_offset": 45,
        "salary_during": 620,
        "operator_positions": 500,
        "supervisor_positions": 45,
        "description": "Evento de categoría mundial. Capacitación previa obligatoria el día anterior.",
    },
    {
        "name": "Lucha Libre AAA — TripleMania XXXIV",
        "event_type": "Lucha Libre",
        "days_offset": -5,
        "salary_during": 420,
        "operator_positions": 180,
        "supervisor_positions": 15,
        "description": "Evento masivo en arena cubierta. Se requiere disponibilidad de 14:00 a 24:00.",
    },
    {
        "name": "Concierto Bad Bunny — Debí Tirar Más Fotos Tour",
        "event_type": "Concierto",
        "days_offset": 30,
        "salary_during": 560,
        "operator_positions": 410,
        "supervisor_positions": 36,
        "description": "Tour internacional de alta demanda. Reclutamiento urgente.",
    },
    {
        "name": "Copa MX — Semifinal",
        "event_type": "Fútbol",
        "days_offset": 21,
        "salary_during": 460,
        "operator_positions": 200,
        "supervisor_positions": 18,
        "description": "Partido de eliminación directa. Reforzar accesos norte y sur.",
    },
]

N_APPLICANTS_RANGE = (8, 18)

# ─────────────────────────────────────────────────────────────────────────────

def random_token():
    return secrets.token_hex(20)

def aplicant_count_for(ev_name):
    """Eventos más grandes → más postulantes."""
    big = ["Coldplay", "F1", "Bad Bunny"]
    if any(k in ev_name for k in big):
        return random.randint(14, 20)
    return random.randint(6, 12)

def main():
    print(f"Conectando a: {DSN[:40]}...")
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:

            # 1. Listar clientes
            cur.execute("SELECT id, slug, name FROM clients ORDER BY id")
            clients = cur.fetchall()
            if not clients:
                print("❌ No hay clientes en la BD. Crea al menos uno primero.")
                return

            print(f"\nClientes encontrados ({len(clients)}):")
            for cid, slug, name in clients:
                print(f"   [{cid}] {slug} - {name}")

            # 2. Para cada cliente, insertar eventos y postulantes
            today = date.today()
            total_events = 0
            total_apps = 0

            for cid, slug, name in clients:
                print(f"\nProcesando cliente: {name} ({slug})")

                # Seleccionar 3-4 eventos aleatorios para este cliente
                sample_events = random.sample(EVENTOS_DEMO, k=min(4, len(EVENTOS_DEMO)))

                for ev_data in sample_events:
                    ev_date = today + timedelta(days=ev_data["days_offset"])

                    cur.execute("""
                        INSERT INTO events (
                            client_id, name, event_date, event_type, description,
                            salary_before, salary_during, salary_after,
                            supervisor_salary_before, supervisor_salary_during, supervisor_salary_after,
                            operator_positions, supervisor_positions,
                            supervisor_before, supervisor_during, supervisor_after
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            320, %s, 360,
                            450, 650, 500,
                            %s, %s,
                            true, true, false
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """, (
                        cid,
                        ev_data["name"],
                        ev_date,
                        ev_data["event_type"],
                        ev_data["description"],
                        ev_data["salary_during"],
                        ev_data["operator_positions"],
                        ev_data["supervisor_positions"],
                    ))
                    row = cur.fetchone()
                    if not row:
                        # Ya existía, buscar id
                        cur.execute(
                            "SELECT id FROM events WHERE client_id=%s AND name=%s",
                            (cid, ev_data["name"])
                        )
                        row = cur.fetchone()
                        if not row:
                            continue
                        ev_id = row[0]
                        print(f"   [!] Evento ya existe, id={ev_id}: {ev_data['name'][:40]}")
                    else:
                        ev_id = row[0]
                        total_events += 1
                        print(f"   [+] Evento creado id={ev_id}: {ev_data['name'][:40]}")

                    # Postulantes para este evento
                    n = aplicant_count_for(ev_data["name"])
                    names_sample = random.sample(NOMBRES, k=min(n, len(NOMBRES)))

                    for fn in names_sample:
                        status = random.choice(STATUSES)
                        area = random.choice(AREAS)
                        phone = random.choice(TELEFONOS)
                        token = random_token()

                        cur.execute("""
                            INSERT INTO applications (
                                event_id, token, full_name, phone, area, status
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (token) DO NOTHING
                        """, (ev_id, token, fn, phone, area, status))
                        total_apps += 1

                conn.commit()

            print(f"\nSeed completado:")
            print(f"   {total_events} eventos creados")
            print(f"   {total_apps} postulantes creados")
            print(f"\n   Abre http://127.0.0.1:8088/reclut para verlo.\n")

if __name__ == "__main__":
    main()
