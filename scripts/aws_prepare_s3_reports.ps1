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

if (-not $BucketName) {
  $suffix = (Get-Random -Minimum 100000 -Maximum 999999)
  $BucketName = "olinckbotai-agent-reports-$suffix"
}

Write-Host "Preparing AWS S3 agent report storage..."
Write-Host "Region: $Region"
Write-Host "Bucket: $BucketName"

aws cloudformation deploy `
  --region $Region `
  --stack-name $StackName `
  --template-file "infra/aws/s3-agent-reports.cloudformation.yml" `
  --parameter-overrides "ReportsBucketName=$BucketName" `
  --capabilities CAPABILITY_NAMED_IAM

Write-Host ""
Write-Host "AWS S3 reports are ready."
Write-Host "Set these values in your environment:"
Write-Host "AWS_REGION=$Region"
Write-Host "AWS_S3_REPORTS_BUCKET=$BucketName"
