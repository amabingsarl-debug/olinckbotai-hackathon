param(
  [string]$ClusterName = $env:COCKROACH_CLUSTER_NAME,
  [string]$DatabaseName = "olinck_agent_memory",
  [string]$SqlFile = "scripts/cockroach_agent_memory_setup.sql"
)

if (-not $ClusterName) {
  Write-Error "Set COCKROACH_CLUSTER_NAME before running this script."
  exit 1
}

Write-Host "Checking CockroachDB Cloud authentication with ccloud..."
ccloud auth whoami

Write-Host "Creating database if needed..."
ccloud sql --cluster $ClusterName --execute "CREATE DATABASE IF NOT EXISTS $DatabaseName;"

Write-Host "Applying agent memory schema and distributed vector index..."
ccloud sql --cluster $ClusterName --database $DatabaseName --file $SqlFile

Write-Host "CockroachDB agent memory is ready for OlinckBotAI."
