param(
  [string]$AwsRegion = $env:AWS_REGION,
  [string]$EcrRepository = $env:AWS_ECR_REPOSITORY,
  [string]$EcsCluster = $env:AWS_ECS_CLUSTER,
  [string]$EcsService = $env:AWS_ECS_SERVICE
)

if (-not $AwsRegion) { $AwsRegion = "us-east-1" }
if (-not $EcrRepository -or -not $EcsCluster -or -not $EcsService) {
  Write-Host "Set AWS_ECR_REPOSITORY, AWS_ECS_CLUSTER and AWS_ECS_SERVICE to deploy."
  Write-Host "This outline intentionally does not store credentials or secrets."
  exit 0
}

Write-Host "Build and push the FastAPI image to ECR, then update ECS Fargate."
Write-Host "1. aws ecr get-login-password --region $AwsRegion | docker login ..."
Write-Host "2. docker build -t olinckbotai-backend ./backend"
Write-Host "3. docker tag olinckbotai-backend:latest $EcrRepository:latest"
Write-Host "4. docker push $EcrRepository:latest"
Write-Host "5. aws ecs update-service --cluster $EcsCluster --service $EcsService --force-new-deployment --region $AwsRegion"
