$ErrorActionPreference = "Stop"

$ProjectId = "desplieguecrmquejas"
$Region = "us-central1"
$CloudSqlInstance = "eventos"
$DatabaseName = "postgres"
$DatabaseUser = "postgres"
$DatabasePassword = "Alpreb123batia+"
$ServiceName = "control-jornales"
$ArtifactRepo = "control-jornales"
$JwtSecret = "control-jornales-session-key"
$AllowUnauthenticated = $true
$Gcloud = "gcloud.cmd"

function Invoke-Gcloud {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    & $Gcloud @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed: $($Arguments -join ' ')"
    }
}

function Test-GcloudResource {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $nativePreferenceExists = $null -ne (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)
    if ($nativePreferenceExists) {
        $previousNativePreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        $ErrorActionPreference = "Continue"
        & $Gcloud @Arguments *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($nativePreferenceExists) {
            $PSNativeCommandUseErrorActionPreference = $previousNativePreference
        }
    }
}

Write-Host "Setting project: $ProjectId"
Invoke-Gcloud @("config", "set", "project", $ProjectId)

Write-Host "Enabling required APIs"
Invoke-Gcloud @("services", "enable", "run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com", "sqladmin.googleapis.com")

Write-Host "Checking Artifact Registry repository: $ArtifactRepo"
$repoExists = Test-GcloudResource @("artifacts", "repositories", "describe", $ArtifactRepo, "--location", $Region, "--project", $ProjectId)
if (-not $repoExists) {
    Invoke-Gcloud @("artifacts", "repositories", "create", $ArtifactRepo, "--repository-format", "docker", "--location", $Region, "--project", $ProjectId)
}

$projectNumber = (& $Gcloud projects describe $ProjectId --format "value(projectNumber)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($projectNumber)) {
    throw "Could not resolve project number for $ProjectId"
}

$runtimeServiceAccount = "$projectNumber-compute@developer.gserviceaccount.com"
Write-Host "Granting Cloud SQL Client role to Cloud Run runtime service account"
Invoke-Gcloud @(
    "projects", "add-iam-policy-binding", $ProjectId,
    "--member", "serviceAccount:$runtimeServiceAccount",
    "--role", "roles/cloudsql.client"
)

$image = "$Region-docker.pkg.dev/$ProjectId/$ArtifactRepo/$ServiceName`:latest"
$cloudSqlConnectionName = "$ProjectId`:$Region`:$CloudSqlInstance"
$encodedUser = [uri]::EscapeDataString($DatabaseUser)
$encodedPassword = [uri]::EscapeDataString($DatabasePassword)
$databaseUrl = "postgresql+psycopg://$encodedUser`:$encodedPassword@/$DatabaseName`?host=/cloudsql/$cloudSqlConnectionName"

Write-Host "Building container image: $image"
Invoke-Gcloud @("builds", "submit", "--tag", $image, "--project", $ProjectId)

$authFlag = if ($AllowUnauthenticated) { "--allow-unauthenticated" } else { "--no-allow-unauthenticated" }

Write-Host "Deploying Cloud Run service: $ServiceName"
Invoke-Gcloud @(
    "run", "deploy", $ServiceName,
    "--image", $image,
    "--region", $Region,
    "--platform", "managed",
    $authFlag,
    "--add-cloudsql-instances", $cloudSqlConnectionName,
    "--set-env-vars", "DATABASE_URL=$databaseUrl,ENVIRONMENT=production,JWT_SECRET=$JwtSecret",
    "--project", $ProjectId
)

Write-Host ""
Write-Host "Deployment complete."
Invoke-Gcloud @("run", "services", "describe", $ServiceName, "--region", $Region, "--project", $ProjectId, "--format", "value(status.url)")
