$ErrorActionPreference = "Stop"

# PostgreSQL destino. Para local con Cloud SQL IP publica o proxy, ajusta host/puerto aqui.
$DatabaseHost = "127.0.0.1"
$DatabasePort = "15432"
$DatabaseName = "postgres"
$DatabaseUser = "postgres"
$DatabasePassword = "Alpreb123batia+"

# SQL Server origen SINGA.
$env:SINGA_SQLSERVER_SERVER = "192.168.12.3"
$env:SINGA_SQLSERVER_DATABASE = "SINGA"
$env:SINGA_SQLSERVER_USERNAME = "alpreb"
$env:SINGA_SQLSERVER_PASSWORD = "s1st3m4s@_"
$env:PAYROLL_YEAR = "2026"

$encodedUser = [uri]::EscapeDataString($DatabaseUser)
$encodedPassword = [uri]::EscapeDataString($DatabasePassword)
$env:DATABASE_URL = "postgresql+psycopg://$encodedUser`:$encodedPassword@$DatabaseHost`:$DatabasePort/$DatabaseName"

python -m pip install -r requirements_migration.txt
python migrate_periodonomina.py
