$ErrorActionPreference = "Stop"

# PostgreSQL destino. Para local con Cloud SQL IP publica, cambia host/puerto aqui.
$DatabaseHost = "127.0.0.1"
$DatabasePort = "15432"
$DatabaseName = "postgres"
$DatabaseUser = "postgres"
$DatabasePassword = "Alpreb123batia+"

# Cliente central donde se guarda SINGA una sola vez.
$ClientSlug = "singa"

$encodedUser = [uri]::EscapeDataString($DatabaseUser)
$encodedPassword = [uri]::EscapeDataString($DatabasePassword)
$env:DATABASE_URL = "postgresql+psycopg://$encodedUser`:$encodedPassword@$DatabaseHost`:$DatabasePort/$DatabaseName"
$env:CLIENT_SLUG = $ClientSlug

python -m pip install -r requirements_migration.txt
python migrate_singa.py
