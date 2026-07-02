#!/bin/bash
set -euo pipefail

REGION="${region}"
PROJECT="${project}"
ECR_REPO_URL="${ecr_repo_url}"
ASSETS_BUCKET="${assets_bucket}"
DB_HOST="${db_host}"
DB_NAME="${db_name}"
DB_USER="${db_username}"
REDIS_HOST="${redis_host}"
SITE_ADDRESS="${site_address}"

# --- Docker + compose plugin ---
dnf update -y
dnf install -y docker awscli
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# --- 2 GB swap (1 GB RAM is tight for web+worker+beat) ---
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

APP_DIR=/opt/deal-hunter
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# --- Pull runtime files from the asset bucket ---
aws s3 cp "s3://$ASSETS_BUCKET/docker-compose.prod.yml" docker-compose.prod.yml --region "$REGION"
aws s3 cp "s3://$ASSETS_BUCKET/Caddyfile" Caddyfile --region "$REGION"

# --- Fetch a secret from SSM ---
getp() { aws ssm get-parameter --with-decryption --region "$REGION" --name "/$PROJECT/prod/$1" --query Parameter.Value --output text; }

DB_PASSWORD="$(getp DB_PASSWORD)"
JWT_SECRET="$(getp JWT_SECRET)"
EBAY_CLIENT_ID="$(getp EBAY_CLIENT_ID)"
EBAY_CLIENT_SECRET="$(getp EBAY_CLIENT_SECRET)"
RAPIDAPI_KEY="$(getp RAPIDAPI_KEY)"
SMTP_HOST="$(getp SMTP_HOST)"
SMTP_PORT="$(getp SMTP_PORT)"
SMTP_USERNAME="$(getp SMTP_USERNAME)"
SMTP_PASSWORD="$(getp SMTP_PASSWORD)"
EMAIL_FROM="$(getp EMAIL_FROM)"

# --- Render .env ---
cat > "$APP_DIR/.env" <<EOF
ENV=production
LOG_LEVEL=info
ECR_IMAGE=$ECR_REPO_URL:latest
SITE_ADDRESS=$SITE_ADDRESS
APP_BASE_URL=$SITE_ADDRESS
DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASSWORD@$DB_HOST:5432/$DB_NAME
REDIS_URL=redis://$REDIS_HOST:6379/0
JWT_SECRET=$JWT_SECRET
EBAY_ENV=production
EBAY_CLIENT_ID=$EBAY_CLIENT_ID
EBAY_CLIENT_SECRET=$EBAY_CLIENT_SECRET
RAPIDAPI_KEY=$RAPIDAPI_KEY
SMTP_HOST=$SMTP_HOST
SMTP_PORT=$SMTP_PORT
SMTP_USERNAME=$SMTP_USERNAME
SMTP_PASSWORD=$SMTP_PASSWORD
EMAIL_FROM=$EMAIL_FROM
SCRAPER_ENABLED=false
EBAY_SCRAPER_ENABLED=false
SHEIN_SCRAPER_ENABLED=false
EOF
chmod 600 "$APP_DIR/.env"

# --- ECR login + first boot ---
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_REPO_URL"

# Wait for RDS to accept connections before migrating (up to ~5 min).
for i in $(seq 1 30); do
  if timeout 5 bash -c "cat < /dev/null > /dev/tcp/$DB_HOST/5432" 2>/dev/null; then break; fi
  sleep 10
done

# On very first boot there may be no image yet; the CI deploy will run compose.
# If an image exists, bring the stack up (idempotent).
if docker pull "$ECR_REPO_URL:latest" 2>/dev/null; then
  docker compose -f docker-compose.prod.yml run --rm web alembic upgrade head || true
  docker compose -f docker-compose.prod.yml up -d
fi
