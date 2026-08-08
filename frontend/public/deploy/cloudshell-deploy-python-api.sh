#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="olinckbotai"
REGION="europe-west1"
SERVICE="olinckbotai-python-api"
INSTANCE="olinck-postgres"
DB_NAME="olinck"
DB_USER="olinck"
DB_PASSWORD="$(openssl rand -base64 24 | tr '+/' 'Ab' | cut -c1-24)"
CONNECTION_NAME="$PROJECT_ID:$REGION:$INSTANCE"
DATABASE_URL="postgresql+asyncpg://$DB_USER:$DB_PASSWORD@/$DB_NAME?host=/cloudsql/$CONNECTION_NAME"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com

if ! gcloud sql instances describe "$INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud sql instances create "$INSTANCE" \
    --project "$PROJECT_ID" \
    --database-version POSTGRES_16 \
    --edition enterprise \
    --region "$REGION" \
    --tier db-f1-micro \
    --storage-size 10 \
    --availability-type ZONAL \
    --storage-auto-increase
fi

if ! gcloud sql databases describe "$DB_NAME" --instance "$INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud sql databases create "$DB_NAME" --instance "$INSTANCE" --project "$PROJECT_ID"
fi

if ! gcloud sql users list --instance "$INSTANCE" --project "$PROJECT_ID" --format="value(name)" | grep -qx "$DB_USER"; then
  gcloud sql users create "$DB_USER" --instance "$INSTANCE" --project "$PROJECT_ID" --password "$DB_PASSWORD"
else
  gcloud sql users set-password "$DB_USER" --instance "$INSTANCE" --project "$PROJECT_ID" --password "$DB_PASSWORD"
fi

rm -rf "$HOME/olinck-bot-cloudrun"
mkdir -p "$HOME/olinck-bot-cloudrun"
cd "$HOME/olinck-bot-cloudrun"
curl -L "https://olinckbotai.web.app/deploy/olinck-backend-cloudrun.zip" -o backend.zip
unzip -o backend.zip

cat > cloudrun-env.yaml <<EOF
ENVIRONMENT: "production"
TRADING_MODE: "paper"
REAL_TRADING_ENABLED: "false"
DATABASE_URL: "$DATABASE_URL"
REDIS_URL: "redis://localhost:6379/0"
SECRET_KEY: "$(openssl rand -hex 32)"
ALLOWED_ORIGINS: '["https://olinckbotai.web.app"]'
EOF

gcloud run deploy "$SERVICE" \
  --source backend \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --add-cloudsql-instances "$CONNECTION_NAME" \
  --env-vars-file cloudrun-env.yaml

API_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')"
echo ""
echo "Migration Cloud Run terminee."
echo "PYTHON_API_URL=$API_URL/api"
echo ""
echo "Reviens dans Codex et envoie cette ligne PYTHON_API_URL pour que je bascule le site Firebase vers ce backend."
