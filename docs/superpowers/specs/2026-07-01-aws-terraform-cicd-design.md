# AWS Free-Tier Deploy: Terraform + CI/CD — Design

**Date:** 2026-07-01
**Status:** Approved (design), pending implementation plan
**Repo:** `djsurt/product-tracker`

## Goal

Deploy Deal Hunter (FastAPI web + Celery worker + Celery beat, backed by
Postgres + Redis) to AWS on the 12-month Free Tier, provisioned with Terraform
and shipped via a GitHub Actions CI/CD pipeline. Primary driver: a professional,
resume-strengthening deployment that demonstrates AWS breadth (VPC, managed data
services, IAM, SSM), Infrastructure-as-Code, and CI/CD — while staying at ~$0/mo
for the free-tier window.

Non-goals: autoscaling, multi-AZ high availability, blue/green deploys, a custom
domain. These are explicitly out of scope (YAGNI for a personal/portfolio
project) and noted as possible future work.

## Architecture Overview

```
GitHub (djsurt/product-tracker)
  │  push/PR ──► ci.yml: pytest (Postgres+Redis service containers)
  │  push main ─► deploy.yml: OIDC ► build ► ECR push ► SSM Run Command
  ▼
AWS (single region, e.g. us-east-1)
  VPC 10.20.0.0/16
   ├─ public subnet  ─ EC2 t3.micro (Docker: caddy + web + worker + beat)
   │                     ▲ 80/443 from internet, NO port 22 (SSM only)
   ├─ private subnet A ─ RDS db.t3.micro Postgres  (5432 from EC2 SG only)
   └─ private subnet B ─ ElastiCache cache.t3.micro Redis (6379 from EC2 SG only)
  ECR repo ─ app image        SSM Parameter Store ─ app secrets (SecureString)
  S3 + DynamoDB ─ Terraform state/lock    AWS Budgets ─ $1 alarm
```

### Why this shape stays free

- **No NAT gateway** (~$32/mo avoided): only the EC2 instance needs outbound
  internet (ECR pulls, Let's Encrypt); it lives in a public subnet with a public
  IP via an Internet Gateway. RDS and ElastiCache never initiate outbound, so
  they sit in private subnets with no NAT.
- **No Application Load Balancer** (~$16/mo avoided): TLS terminates on a Caddy
  container on the box (auto Let's Encrypt), serving directly on 80/443.
- **Free-Tier compute/data**: EC2 `t3.micro`, RDS `db.t3.micro` (single-AZ,
  20 GB gp2), ElastiCache `cache.t3.micro` — each one always-on instance stays
  under the 750 hrs/month Free-Tier allotment.
- **Negligible-cost services**: ECR (500 MB free), S3 + DynamoDB (state), SSM
  Parameter Store (standard params free), AWS Budgets (free).

## Components

### 1. Network (`infra/network.tf`)
- VPC `10.20.0.0/16`.
- 1 public subnet (`10.20.1.0/24`) + Internet Gateway + public route table.
- 2 private subnets (`10.20.11.0/24`, `10.20.12.0/24`) across two AZs — required
  because RDS and ElastiCache subnet groups need ≥2 AZs. No NAT, no public route.

### 2. Compute (`infra/ec2.tf`, `infra/user_data.sh`)
- EC2 `t3.micro`, Amazon Linux 2023 (SSM agent preinstalled), in the public
  subnet with a public IP and an instance profile (see IAM).
- `user_data.sh` on first boot: install Docker + compose plugin; create a 2 GB
  swapfile (insurance for 1 GB RAM running 3 Python processes); authenticate to
  ECR via the instance role; fetch config from SSM + Terraform-provided endpoints
  to render `/opt/deal-hunter/.env`; `docker compose -f docker-compose.prod.yml up -d`.
- Celery pinned to low concurrency (`--concurrency=2`) to fit the box.

### 3. Data
- **RDS** (`infra/rds.tf`): `db.t3.micro` Postgres, single-AZ, 20 GB, in private
  subnets, `publicly_accessible = false`. Master password generated via
  `random_password` and published to SSM SecureString for the app to consume.
  Note: the password unavoidably also lives in Terraform state — this is the
  reason the S3 state backend is encrypted and access-locked, and why state is
  never committed to git.
- **ElastiCache** (`infra/elasticache.tf`): single-node `cache.t3.micro` Redis in
  private subnets.

### 4. Container registry (`infra/ecr.tf`)
- One ECR repository for the app image. Lifecycle policy keeps the last ~5 images
  to stay under the 500 MB free storage.

### 5. Runtime containers (`docker-compose.prod.yml`, `Caddyfile`)
- Mirrors `docker-compose.yml` but: uses the **ECR image** (no local build) for
  `web`/`worker`/`beat`; **drops** `postgres`, `redis`, `mock-store`, `mailhog`
  (RDS/ElastiCache replace the first two; the mock store and MailHog are dev-only);
  adds a **caddy** service reverse-proxying to `web` with automatic HTTPS.
- `DATABASE_URL` / `REDIS_URL` point at the RDS / ElastiCache endpoints.
- **Caddy** obtains a Let's Encrypt cert for `deal-hunter.<ec2-public-ip>.sslip.io`
  (sslip.io resolves the embedded IP, so no domain purchase). `APP_BASE_URL` is set
  to that HTTPS URL so alert-email `/go/{offer_id}` links resolve.

### 6. Secrets & config (`infra/ssm.tf`)
- App secrets — `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `RAPIDAPI_KEY`,
  `JWT_SECRET`, `SMTP_*`, `EMAIL_FROM` — stored as **SSM Parameter Store
  SecureStrings** under `/deal-hunter/prod/*`. Terraform declares the parameter
  *names* (and non-secret defaults); secret *values* are populated out-of-band by
  the user (helper script / console) so they never land in git or tf state.
- The EC2 instance role can read only `/deal-hunter/prod/*`.

### 7. IAM (`infra/iam_github_oidc.tf`, instance profile in `ec2.tf`)
- **EC2 instance role:** `AmazonSSMManagedInstanceCore` (Session Manager +
  Run Command), ECR read-only, and scoped `ssm:GetParameter(s)` on
  `/deal-hunter/prod/*`.
- **GitHub OIDC role:** trust policy limited to
  `repo:djsurt/product-tracker:ref:refs/heads/main` (and PRs for read-only plan if
  ever needed). Permissions: ECR push and `ssm:SendCommand` to the tagged instance.
  No static AWS keys stored in GitHub.

### 8. CI/CD (`.github/workflows/ci.yml`, `deploy.yml`)
- **ci.yml** — on pull_request and push: launch Postgres + Redis as Actions
  service containers, install deps, run `alembic upgrade head`, run `pytest`
  (all 89 tests). Required status check for merging.
- **deploy.yml** — on push to `main` (needs ci green): `aws-actions/
  configure-aws-credentials` via OIDC → `docker build` → tag with the commit SHA
  + `latest` → push to ECR → `aws ssm send-command` instructing the instance to
  `docker compose pull`, `docker compose run --rm web alembic upgrade head`, then
  `docker compose up -d`. Deploy status surfaced back in the workflow.

### 9. State & cost (`infra/backend.tf`, `infra/budgets.tf`)
- **Remote state:** S3 bucket (versioned, encrypted) + DynamoDB lock table.
  Bootstrapped once (documented) since state can't store its own backend.
- **Budget alarm:** AWS Budgets monthly cost budget at **$1** emailing the user —
  an early tripwire for anything that escapes the Free Tier.
- **Teardown:** `terraform destroy` returns to $0 (plus manual emptying of the
  versioned state bucket if the whole project is being deleted).

## Deploy Flow (end state)

1. **One-time infra:** user runs `terraform init/apply` locally → VPC, EC2, RDS,
   ElastiCache, ECR, IAM, SSM param names, budget. Outputs: EC2 public IP, ECR
   URL, sslip.io hostname, role ARNs.
2. **One-time secrets:** user populates `/deal-hunter/prod/*` SecureStrings (helper
   script provided) and sets GitHub repo variables (AWS account ID, region, ECR
   repo, instance ID, OIDC role ARN).
3. **Every push to main:** CI runs tests; on green, deploy builds + pushes the
   image and triggers the SSM deploy. First deploy brings the stack up; Caddy
   fetches TLS; the app is live at the HTTPS sslip.io URL.

## Testing Strategy

- **App tests** unchanged (89 passing) — now also run in `ci.yml` on every push,
  which is the regression gate for deploys.
- **Terraform:** `terraform fmt -check` and `terraform validate` in CI (and/or a
  pre-apply local check); `terraform plan` reviewed before every `apply`.
- **Smoke test:** after deploy, `deploy.yml` curls the `/health` endpoint through
  the public HTTPS URL and fails the job if it isn't `{"status":"ok"}`.
- **Cost test:** confirm the AWS Billing console shows $0 under Free Tier after
  the first day, and that the $1 budget alarm is armed.

## Risks & Mitigations

- **1 GB RAM pressure** (web+worker+beat+caddy): 2 GB swapfile, low Celery
  concurrency, drop dev-only containers. If still tight, move `beat` into the
  worker via `--beat` (one process).
- **Free-Tier expiry (12 months):** documented; after that the topology is
  ~$25–35/mo, so `terraform destroy` when the demo period ends. Budget alarm
  catches accidental overage sooner.
- **sslip.io / Let's Encrypt dependency:** if sslip.io is down or rate-limited,
  fall back to HTTP or a real domain. Low risk for a demo.
- **First-boot ordering:** RDS can take ~10 min to become available; `user_data`
  and the deploy script must wait/retry on DB connectivity before migrating.

## Future Work (out of scope now)

Custom domain + Route 53; full GitOps (Terraform plan/apply in Actions);
CloudWatch dashboards/alarms; container image scanning; blue/green or rolling
deploys; moving to ECS/Fargate if always-on scaling is ever needed.
