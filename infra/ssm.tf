resource "random_password" "db" {
  length  = 24
  special = false
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

# Real generated secrets (Terraform owns the value).
resource "aws_ssm_parameter" "db_password" {
  name  = "/${var.project}/prod/DB_PASSWORD"
  type  = "SecureString"
  value = random_password.db.result
}

resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/${var.project}/prod/JWT_SECRET"
  type  = "SecureString"
  value = random_password.jwt.result
}

# User-supplied secrets: created with a placeholder, set out-of-band via
# scripts/put-secrets.sh. Terraform ignores value drift after creation.
locals {
  user_secrets = [
    "EBAY_CLIENT_ID",
    "EBAY_CLIENT_SECRET",
    "RAPIDAPI_KEY",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "EMAIL_FROM",
  ]
}

resource "aws_ssm_parameter" "user_secret" {
  for_each = toset(local.user_secrets)
  name     = "/${var.project}/prod/${each.key}"
  type     = "SecureString"
  value    = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}
