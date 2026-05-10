# Control de Jornales

Aplicacion Python lista para un despliegue unico en Cloud Run:

- Backend: FastAPI, SQLAlchemy 2, Alembic.
- Frontend: HTML/CSS/JS estatico servido por FastAPI.
- Base de datos: Cloud SQL PostgreSQL administrada fuera de la app.
- Archivos INE: se modelan con `ine_filename` y `ine_gcs_uri`; en produccion deben subirse a Cloud Storage, no al filesystem de Cloud Run.

## Desarrollo local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Antes de arrancar la app, la base de datos de `DATABASE_URL` debe existir y contener las tablas y el usuario inicial. Al iniciar, la app agrega automaticamente las columnas nuevas de jornales si faltan.

## Importar empleados Ollamani

El script `migrate_ollamani.py` lee `tb_empleado` desde SINGA con `id_cliente = 2401` e `id_status = 2`, y los guarda en el cliente `ollamani` con `source = 'ollamani'`.

```powershell
$env:DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB"
python migrate_ollamani.py
```

Variables opcionales:

- `OLLAMANI_SINGA_CLIENT_ID`: por defecto `2401`.
- `OLLAMANI_SINGA_STATUS_ID`: por defecto `2`.
- `OLLAMANI_CLIENT_SLUG`: por defecto `ollamani`.

## Importar layout de jornales

En la vista **Base de Jornales**, usa **Layout** para descargar el formato y **Importar** para cargarlo. El archivo debe conservar estos encabezados en el mismo orden:

```text
Numero, Nombre, Area, Tipo, Telefono, Telefono 2, Contacto/Redes, Banco, Cuenta, CLABE, INE, Veto, Fecha veto, Motivo veto, Fecha desmarque, Motivo desmarque
```

`Nombre` y `Area` son obligatorios. `Tipo` acepta `Jornal` o `Supervisor`; los supervisores requieren `Numero`. Si `Numero` ya existe en el cliente, se actualiza el registro de plataforma; si no existe, se crea.

## Variables para Cloud Run

Configura estas variables o secrets:

- `DATABASE_URL`: conexion SQLAlchemy a Cloud SQL PostgreSQL ya preparado.
- `JWT_SECRET`: valor largo generado en Secret Manager.
- `ACCESS_TOKEN_MINUTES`: duracion de sesion.
- `GCS_BUCKET`: bucket para documentos INE cuando se implemente carga real de archivos.

Para Cloud SQL con Unix socket:

```text
postgresql+psycopg://USER:PASSWORD@/DATABASE?host=/cloudsql/PROJECT:REGION:INSTANCE
```

## Despliegue Cloud Run

```powershell
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/REPO/control-jornales:latest
gcloud run deploy control-jornales `
  --image REGION-docker.pkg.dev/PROJECT/REPO/control-jornales:latest `
  --region REGION `
  --add-cloudsql-instances PROJECT:REGION:INSTANCE `
  --set-env-vars DATABASE_URL="postgresql+psycopg://USER:PASSWORD@/DATABASE?host=/cloudsql/PROJECT:REGION:INSTANCE" `
  --set-secrets JWT_SECRET=jwt-secret:latest
```

El contenedor no crea tablas nuevas. En el arranque agrega columnas faltantes de jornales con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
