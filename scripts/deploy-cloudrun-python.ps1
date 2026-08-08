$ErrorActionPreference = "Stop"

$ProjectId = "olinckbotai"
$Region = "europe-west1"
$Service = "olinckbotai-python-api"
$Instance = "olinck-postgres"
$DbName = "olinck"
$DbUser = "olinck"
$PasswordFile = Join-Path $PSScriptRoot "cloudsql-password.txt"
$SchedulerSecretFile = Join-Path $PSScriptRoot "scheduler-secret.txt"

if (!(Test-Path $PasswordFile)) {
  $bytes = New-Object byte[] 24
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  [Convert]::ToBase64String($bytes).Replace("+", "A").Replace("/", "b").Substring(0, 24) | Set-Content -NoNewline $PasswordFile
}

$DbPassword = Get-Content $PasswordFile -Raw
if (!(Test-Path $SchedulerSecretFile)) {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  [Convert]::ToBase64String($bytes).Replace("+", "A").Replace("/", "b").Replace("=", "") | Set-Content -NoNewline $SchedulerSecretFile
}
$SchedulerSecret = Get-Content $SchedulerSecretFile -Raw
$ConnectionName = "$ProjectId`:$Region`:$Instance"
$DatabaseUrl = "postgresql+asyncpg://$DbUser`:$DbPassword@/${DbName}?host=/cloudsql/$ConnectionName"
$EnvFile = Join-Path $PSScriptRoot "cloudrun-env.yaml"

$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (!(Test-Path $gcloud)) {
  throw "Google Cloud SDK introuvable. Installe gcloud ou lance depuis Cloud Shell."
}

& $gcloud config set project $ProjectId
& $gcloud services enable run.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com

$instanceExists = & $gcloud sql instances describe $Instance --project $ProjectId --format="value(name)" 2>$null
if (!$instanceExists) {
  & $gcloud sql instances create $Instance `
    --project $ProjectId `
    --database-version POSTGRES_16 `
    --edition enterprise `
    --region $Region `
    --tier db-f1-micro `
    --storage-size 10 `
    --availability-type ZONAL `
    --storage-auto-increase
} else {
  & $gcloud sql instances patch $Instance `
    --project $ProjectId `
    --activation-policy ALWAYS `
    --quiet
}

$databaseExists = & $gcloud sql databases describe $DbName --instance $Instance --project $ProjectId --format="value(name)" 2>$null
if (!$databaseExists) {
  & $gcloud sql databases create $DbName --instance $Instance --project $ProjectId
}

$userExists = & $gcloud sql users list --instance $Instance --project $ProjectId --format="value(name)" | Select-String -SimpleMatch $DbUser
if (!$userExists) {
  & $gcloud sql users create $DbUser --instance $Instance --project $ProjectId --password $DbPassword
} else {
  & $gcloud sql users set-password $DbUser --instance $Instance --project $ProjectId --password $DbPassword
}

@"
ENVIRONMENT: "production"
TRADING_MODE: "paper"
REAL_TRADING_ENABLED: "false"
DATABASE_URL: "$DatabaseUrl"
REDIS_URL: "redis://localhost:6379/0"
SECRET_KEY: "$(New-Guid)"
ALLOWED_ORIGINS: '["https://olinckbotai.web.app"]'
SCHEDULER_SECRET: "$SchedulerSecret"
"@ | Set-Content -Encoding UTF8 $EnvFile

& $gcloud run deploy $Service `
  --source backend `
  --project $ProjectId `
  --region $Region `
  --allow-unauthenticated `
  --add-cloudsql-instances $ConnectionName `
  --env-vars-file $EnvFile

& $gcloud run services describe $Service --region $Region --project $ProjectId --format="value(status.url)"
$ServiceUrl = & $gcloud run services describe $Service --region $Region --project $ProjectId --format="value(status.url)"
$Job = "olinck-paper-trading-cycle"
$JobExists = & $gcloud scheduler jobs describe $Job --location $Region --project $ProjectId --format="value(name)" 2>$null
$SchedulerArgs = @(
  "--location", $Region,
  "--project", $ProjectId,
  "--schedule", "*/5 * * * *",
  "--time-zone", "Etc/UTC",
  "--uri", "$ServiceUrl/api/bot/tick",
  "--http-method", "POST",
  "--headers", "X-Scheduler-Secret=$SchedulerSecret",
  "--attempt-deadline", "180s"
)
if ($JobExists) {
  & $gcloud scheduler jobs update http $Job @SchedulerArgs
} else {
  & $gcloud scheduler jobs create http $Job @SchedulerArgs
}
