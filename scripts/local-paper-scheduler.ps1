param(
    [int]$IntervalSeconds = 60,
    [string]$ApiBaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root ".env"
$LogPath = Join-Path $Root "local-paper-scheduler.log"

function Get-EnvValue {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return $null
    }
    $line = Get-Content -LiteralPath $EnvPath | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    if (-not $line) {
        return $null
    }
    return $line -replace "^$Name=", ""
}

$Secret = Get-EnvValue -Name "SCHEDULER_SECRET"
if (-not $Secret) {
    Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) missing SCHEDULER_SECRET"
    exit 1
}

Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) local paper scheduler started, interval=${IntervalSeconds}s"

while ($true) {
    try {
        $response = Invoke-RestMethod `
            -Uri "$ApiBaseUrl/api/bot/tick" `
            -Method Post `
            -Headers @{ "X-Scheduler-Secret" = $Secret } `
            -TimeoutSec 45
        $status = $response | ConvertTo-Json -Compress -Depth 8
        Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) tick ok $status"
    }
    catch {
        Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) tick error $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSeconds
}
