param(
  [string]$Region = $env:AWS_REGION,
  [string]$BucketName = $env:AWS_S3_REPORTS_BUCKET,
  [string]$StackName = "olinckbotai-agent-reports"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Region) {
  $Region = "eu-west-1"
}

Write-Host "OlinckBotAI AWS readiness check"
Write-Host "Region: $Region"

$awsCommand = Get-Command aws -ErrorAction SilentlyContinue
if (-not $awsCommand) {
  Write-Host "AWS CLI: missing"
  Write-Host "Install AWS CLI v2, then run: aws configure sso or aws configure"
  exit 2
}

Write-Host "AWS CLI: $($awsCommand.Source)"

try {
  $identityJson = aws sts get-caller-identity --output json 2>$null
  $identity = $identityJson | ConvertFrom-Json
  Write-Host "Account: $($identity.Account)"
  Write-Host "Principal: $($identity.Arn)"
} catch {
  Write-Host "AWS authentication: not ready"
  Write-Host "Run: aws configure sso"
  Write-Host "Or configure a least-privilege access key outside this repository."
  exit 3
}

try {
  $stackJson = aws cloudformation describe-stacks --region $Region --stack-name $StackName --output json 2>$null
  $stack = ($stackJson | ConvertFrom-Json).Stacks[0]
  Write-Host "CloudFormation stack: $($stack.StackStatus)"
} catch {
  Write-Host "CloudFormation stack: not found yet"
  Write-Host "Create it with: .\scripts\aws_prepare_s3_reports.ps1 -Region $Region -BucketName olinckbotai-agent-reports-<unique-suffix>"
}

if ($BucketName) {
  try {
    aws s3api head-bucket --bucket $BucketName 2>$null | Out-Null
    Write-Host "S3 reports bucket: reachable ($BucketName)"
  } catch {
    Write-Host "S3 reports bucket: not reachable or not created ($BucketName)"
    exit 4
  }
} else {
  Write-Host "S3 reports bucket: not configured in AWS_S3_REPORTS_BUCKET"
}

Write-Host "AWS readiness check complete."
