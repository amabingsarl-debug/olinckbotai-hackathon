$ErrorActionPreference = "Stop"

$ProjectId = "olinckbotai"
$Region = "europe-west1"
$Service = "olinckbotai-python-api"
$FirebaseJson = Join-Path $PSScriptRoot "..\firebase.json"

$config = Get-Content $FirebaseJson -Raw | ConvertFrom-Json
$config.hosting.rewrites = @(
  @{
    source = "/api/**"
    run = @{
      serviceId = $Service
      region = $Region
    }
  },
  @{
    source = "**"
    destination = "/index.html"
  }
)

$config | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $FirebaseJson

Push-Location (Join-Path $PSScriptRoot "..")
try {
  npm.cmd --prefix frontend run build
  firebase.cmd deploy --only hosting --project $ProjectId
} finally {
  Pop-Location
}
