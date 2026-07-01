#!/usr/bin/env bash
# Populate SSM SecureString values for the user-supplied app secrets.
# Reads values from your local .env (KEY=VALUE lines). Run after `terraform apply`.
#   PROJECT=deal-hunter REGION=us-east-1 ./scripts/put-secrets.sh path/to/.env
set -euo pipefail

PROJECT="${PROJECT:-deal-hunter}"
REGION="${REGION:-us-east-1}"
ENV_FILE="${1:-.env}"

KEYS=(EBAY_CLIENT_ID EBAY_CLIENT_SECRET RAPIDAPI_KEY SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD EMAIL_FROM)

for key in "${KEYS[@]}"; do
  val="$(grep -E "^${key}=" "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  if [ -z "$val" ]; then
    echo "skip $key (not in $ENV_FILE)"
    continue
  fi
  aws ssm put-parameter --region "$REGION" --name "/$PROJECT/prod/$key" \
    --type SecureString --value "$val" --overwrite >/dev/null
  echo "set $key"
done
