$ErrorActionPreference = "Stop"

# Pon aqui el IP publico de tu instancia Cloud SQL PostgreSQL.
# En Cloud SQL tambien debes autorizar tu IP actual en "Authorized networks".
$DatabaseHost = "PON_AQUI_EL_IP_PUBLICO_DE_CLOUD_SQL"
$DatabasePort = "5432"
$DatabaseName = "postgres"
$DatabaseUser = "postgres"
$DatabasePassword = "Alpreb123batia+"

$encodedUser = [uri]::EscapeDataString($DatabaseUser)
$encodedPassword = [uri]::EscapeDataString($DatabasePassword)

$env:DATABASE_URL = "postgresql+psycopg://$encodedUser`:$encodedPassword@$DatabaseHost`:$DatabasePort/$DatabaseName"
$env:JWT_SECRET = "control-jornales-session-key-local"
$env:ENVIRONMENT = "development"

Write-Host "Starting local app at http://127.0.0.1:8000"
Write-Host "Database host: $DatabaseHost"

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
