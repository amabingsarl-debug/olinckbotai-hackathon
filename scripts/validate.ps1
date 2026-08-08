Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
  $Python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $Python) {
  Write-Error "Python was not found. Install Python 3.12+ or run validation inside the backend Docker container."
  exit 1
}

Push-Location backend
& $Python.Source -m compileall app tests
& $Python.Source -m pytest
Pop-Location

Push-Location frontend
npm install
npm run build
Pop-Location
