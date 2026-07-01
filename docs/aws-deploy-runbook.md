# Deal Hunter — AWS Deploy Runbook

Prerequisites: an AWS account, AWS CLI + Terraform installed locally, and local
admin credentials (`aws configure` with an IAM user that can create these
resources). All commands run from the repo root unless noted.

## 1. Bootstrap remote state (once)
```bash
cd infra/bootstrap
terraform init
terraform apply        # note the state_bucket + lock_table outputs
cd ../..
```

## 2. Configure the backend + variables
```bash
cp infra/backend.hcl.example infra/backend.hcl
# edit infra/backend.hcl: set bucket to the state_bucket output
cp infra/terraform.tfvars.example infra/terraform.tfvars
# edit infra/terraform.tfvars: set budget_alert_email
```

## 3. Provision infrastructure
```bash
cd infra
terraform init -backend-config=backend.hcl
terraform apply        # ~10-15 min (RDS is the slow part)
terraform output       # record: site_url, ecr_repository_url/name,
                       # instance_id, github_role_arn, region, account_id
cd ..
```

## 4. Load app secrets into SSM
Use your existing local `.env` (with the real eBay + RapidAPI keys).
```bash
PROJECT=deal-hunter REGION=us-east-1 ./scripts/put-secrets.sh .env
```
(If you want email alerts, set the `SMTP_*` and `EMAIL_FROM` values in that
`.env` first — e.g. a Gmail app password — or leave them and add later.)

## 5. Set GitHub repo variables
In GitHub → repo Settings → Secrets and variables → Actions → **Variables**,
add (values from `terraform output`):

| Variable | Value |
|---|---|
| `AWS_REGION` | `region` output |
| `AWS_ACCOUNT_ID` | `account_id` output |
| `AWS_ROLE_ARN` | `github_role_arn` output |
| `ECR_REPOSITORY` | `ecr_repository_name` output |
| `INSTANCE_ID` | `instance_id` output |
| `SITE_URL` | `site_url` output |

## 6. First deploy
Merge to `main` (or push). `ci.yml` runs tests + migration check; on green,
`deploy.yml` builds the image, pushes to ECR, and deploys via SSM. Watch the
Actions tab. First run also lets Caddy fetch a TLS cert (can take a minute).

Visit the `site_url`. Log in / use the app. Confirm `active_sources` includes
`ebay` and `rapidapi` at `<site_url>/`.

## Operations
- **Shell into the box (no SSH):** `aws ssm start-session --target <instance_id> --region <region>`
- **Logs:** on the box, `cd /opt/deal-hunter && docker compose -f docker-compose.prod.yml logs -f worker`
- **Rotate a secret:** `aws ssm put-parameter --name /deal-hunter/prod/<KEY> --type SecureString --value <NEW> --overwrite`, then restart: `docker compose ... up -d --force-recreate`
- **Teardown ($0):** `cd infra && terraform destroy`, then `cd bootstrap && terraform destroy` (empty the versioned state bucket first if destroying bootstrap).

## Cost notes
Free Tier covers this topology for 12 months. The `$1` AWS Budgets alarm emails
you if anything escapes it. After 12 months, expect ~$25-35/mo, so run the
teardown when you're done demoing.
