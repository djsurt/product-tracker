# AWS Free-Tier Deploy (Terraform + CI/CD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision Deal Hunter on AWS Free Tier (EC2 + RDS + ElastiCache in a NAT-free VPC) with Terraform, and ship it via a GitHub Actions CI/CD pipeline using OIDC + ECR + SSM.

**Architecture:** One EC2 `t3.micro` runs Docker (Caddy + web + worker + beat) in a public subnet; managed RDS Postgres and ElastiCache Redis sit in private subnets (no NAT). Terraform runs locally with S3 remote state. GitHub Actions runs tests, then builds/pushes an image to ECR and deploys via SSM Run Command. Caddy provides free HTTPS via sslip.io.

**Tech Stack:** Terraform (`hashicorp/aws ~> 5.0`), AWS (VPC, EC2, RDS, ElastiCache, ECR, IAM/OIDC, SSM, S3, DynamoDB, Budgets), Docker Compose, Caddy 2, GitHub Actions, Python 3.12.

## Global Constraints

- Region default `us-east-1` (Terraform variable `region`); all resources single-region.
- Everything must stay within the **12-month AWS Free Tier**: `t3.micro`, `db.t3.micro`, `cache.t3.micro`, single instance each; **no NAT gateway; no load balancer**.
- **No inbound SSH** anywhere (port 22 closed); admin access via SSM Session Manager only.
- **No static AWS keys** in GitHub; auth via OIDC role assumption.
- Secrets never committed to git and never in plaintext Terraform outputs. App secrets live in SSM Parameter Store SecureStrings under `/deal-hunter/prod/*`.
- GitHub repo: `djsurt/product-tracker`. Deploy triggers only on `main`.
- App image tags: `<git-sha>` and `latest`; deployment pulls `latest`.
- Marketplace HTML scrapers stay disabled in prod (`EBAY_SCRAPER_ENABLED=false`, `SHEIN_SCRAPER_ENABLED=false`, `SCRAPER_ENABLED=false`).

## File Structure

```
.dockerignore                     # keep .env/.git/.venv/tests out of images
docker-compose.prod.yml           # ECR image; caddy+web+worker+beat (no pg/redis/mock/mailhog)
Caddyfile                         # auto-HTTPS reverse proxy to web:8000
scripts/put-secrets.sh            # populate SSM SecureStrings from local values
infra/
  bootstrap/                      # one-time remote-state backing (local state)
    main.tf                       # S3 state bucket + DynamoDB lock table
  backend.hcl.example             # backend config for `terraform init -backend-config`
  providers.tf  variables.tf  backend.tf  data.tf
  network.tf  security.tf  ecr.tf  assets.tf
  ssm.tf  rds.tf  elasticache.tf
  iam_github_oidc.tf  ec2.tf  user_data.sh  budgets.tf  outputs.tf
  terraform.tfvars.example
.github/workflows/ci.yml          # pytest (sqlite/fakeredis) + migration check (postgres)
.github/workflows/deploy.yml      # OIDC -> build -> ECR push -> SSM deploy -> smoke
docs/aws-deploy-runbook.md        # the hands-on steps the user runs
```

**Local validation commands used throughout** (no AWS creds needed):
- Terraform: `cd infra && terraform init -backend=false && terraform fmt -check && terraform validate`
- Shell scripts: `bash -n <file>`
- Compose: `docker compose -f docker-compose.prod.yml config -q` (with a dummy `.env`)
- Workflows: `python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>` (or `actionlint` if installed)

---

### Task 1: Runtime artifacts — `.dockerignore`, prod compose, Caddyfile

**Files:**
- Create: `.dockerignore`
- Create: `docker-compose.prod.yml`
- Create: `Caddyfile`

**Interfaces:**
- Produces: a `web` service on internal port 8000; Caddy listens on 80/443 and reverse-proxies to `web:8000`; services read env from `.env`; image ref via `${ECR_IMAGE}`, public hostname via `${SITE_ADDRESS}`.

- [ ] **Step 1: Create `.dockerignore`**

```
.git
.gitignore
.env
.env.*
.venv
__pycache__/
*.pyc
tests/
docs/
infra/
.github/
*.md
docker-compose*.yml
Caddyfile
```

- [ ] **Step 2: Create `docker-compose.prod.yml`**

```yaml
# Production stack for the EC2 host. Uses the prebuilt ECR image (no build),
# and drops dev-only services (postgres/redis are managed; mock-store/mailhog
# are dev tools). Config comes from /opt/deal-hunter/.env rendered at boot.
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      - SITE_ADDRESS=${SITE_ADDRESS}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - web

  web:
    image: ${ECR_IMAGE}
    restart: unless-stopped
    command: uvicorn api.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    expose:
      - "8000"

  worker:
    image: ${ECR_IMAGE}
    restart: unless-stopped
    command: celery -A workers.celery_app worker --loglevel=info --concurrency=2
    env_file: .env

  beat:
    image: ${ECR_IMAGE}
    restart: unless-stopped
    command: celery -A workers.celery_app beat --loglevel=info
    env_file: .env

volumes:
  caddy_data:
  caddy_config:
```

- [ ] **Step 3: Create `Caddyfile`**

```
# SITE_ADDRESS is a full https URL (e.g. https://deal-hunter.3-91-1-2.sslip.io).
# Caddy auto-provisions a Let's Encrypt cert for it and proxies to the app.
{$SITE_ADDRESS} {
	reverse_proxy web:8000
}
```

- [ ] **Step 4: Validate compose parses**

Run:
```bash
printf 'ECR_IMAGE=example:latest\nSITE_ADDRESS=https://x.sslip.io\n' > .env.prodcheck
docker compose -f docker-compose.prod.yml --env-file .env.prodcheck config -q && echo OK
rm -f .env.prodcheck
```
Expected: prints `OK`, no errors.

- [ ] **Step 5: Commit**

```bash
git add .dockerignore docker-compose.prod.yml Caddyfile
git commit -m "Add prod compose, Caddyfile, and .dockerignore for AWS deploy"
```

---

### Task 2: Terraform remote-state bootstrap

**Files:**
- Create: `infra/bootstrap/main.tf`
- Create: `infra/backend.hcl.example`

**Interfaces:**
- Produces: an S3 bucket `<project>-tfstate-<account_id>` and DynamoDB table `<project>-tflock` that the main config's `backend "s3"` consumes via `-backend-config=backend.hcl`.

- [ ] **Step 1: Create `infra/bootstrap/main.tf`**

```hcl
# One-time bootstrap for Terraform remote state. Uses LOCAL state (this is the
# chicken-and-egg exception). Run `terraform apply` here once, then configure
# the main stack's S3 backend with the outputs.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "deal-hunter"
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "state" {
  bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "lock" {
  name         = "${var.project}-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}
```

- [ ] **Step 2: Create `infra/backend.hcl.example`**

```hcl
# Copy to backend.hcl and fill from bootstrap outputs, then:
#   terraform init -backend-config=backend.hcl
bucket         = "deal-hunter-tfstate-REPLACE_ACCOUNT_ID"
key            = "deal-hunter/prod/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "deal-hunter-tflock"
encrypt        = true
```

- [ ] **Step 3: Validate bootstrap**

Run:
```bash
cd infra/bootstrap && terraform init -backend=false && terraform fmt -check && terraform validate && cd ../..
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 4: Commit**

```bash
git add infra/bootstrap/main.tf infra/backend.hcl.example
git commit -m "Add Terraform remote-state bootstrap (S3 + DynamoDB)"
```

---

### Task 3: Terraform skeleton — providers, variables, backend, shared data

**Files:**
- Create: `infra/providers.tf`
- Create: `infra/backend.tf`
- Create: `infra/variables.tf`
- Create: `infra/data.tf`
- Create: `infra/terraform.tfvars.example`

**Interfaces:**
- Produces: provider `aws` (region `var.region`, default tags); variables `region, project, instance_type, db_instance_class, cache_node_type, github_repo, budget_alert_email, db_name, db_username`; data sources `aws_caller_identity.current`, `aws_availability_zones.available`, `aws_ssm_parameter.al2023` (AMI id).

- [ ] **Step 1: Create `infra/providers.tf`**

```hcl
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
```

- [ ] **Step 2: Create `infra/backend.tf`** (partial config — values via `-backend-config`)

```hcl
terraform {
  backend "s3" {}
}
```

- [ ] **Step 3: Create `infra/variables.tf`**

```hcl
variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "deal-hunter"
}

variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "cache_node_type" {
  type    = string
  default = "cache.t3.micro"
}

variable "github_repo" {
  type    = string
  default = "djsurt/product-tracker"
}

variable "budget_alert_email" {
  type = string
}

variable "db_name" {
  type    = string
  default = "deals"
}

variable "db_username" {
  type    = string
  default = "deals"
}
```

- [ ] **Step 4: Create `infra/data.tf`**

```hcl
data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# Latest Amazon Linux 2023 AMI id, resolved at plan time.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
}
```

- [ ] **Step 5: Create `infra/terraform.tfvars.example`**

```hcl
# Copy to terraform.tfvars and edit.
region             = "us-east-1"
budget_alert_email = "djsurti3003@gmail.com"
```

- [ ] **Step 6: Validate**

Run:
```bash
cd infra && terraform init -backend=false && terraform fmt -check && terraform validate && cd ..
```
Expected: `Success! The configuration is valid.` (validate ignores the empty backend block.)

- [ ] **Step 7: Commit**

```bash
git add infra/providers.tf infra/backend.tf infra/variables.tf infra/data.tf infra/terraform.tfvars.example
git commit -m "Add Terraform skeleton: providers, variables, backend, data sources"
```

---

### Task 4: Network (VPC, subnets, IGW, routes)

**Files:**
- Create: `infra/network.tf`

**Interfaces:**
- Produces: `aws_vpc.main`, `aws_subnet.public`, `aws_subnet.private[0..1]` (used by RDS/ElastiCache subnet groups and the EC2 instance).

- [ ] **Step 1: Create `infra/network.tf`**

```hcl
resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-public" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.1${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "${var.project}-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "${var.project}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: `Success! The configuration is valid.`

- [ ] **Step 3: Commit**

```bash
git add infra/network.tf
git commit -m "Add VPC, public + private subnets, IGW, routing (no NAT)"
```

---

### Task 5: Security groups

**Files:**
- Create: `infra/security.tf`

**Interfaces:**
- Produces: `aws_security_group.web` (80/443 in), `aws_security_group.db` (5432 from web SG), `aws_security_group.cache` (6379 from web SG).

- [ ] **Step 1: Create `infra/security.tf`**

```hcl
resource "aws_security_group" "web" {
  name        = "${var.project}-web"
  description = "EC2 host: HTTP/HTTPS in, all out. No SSH."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-web" }
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "Postgres from the web SG only."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }

  tags = { Name = "${var.project}-db" }
}

resource "aws_security_group" "cache" {
  name        = "${var.project}-cache"
  description = "Redis from the web SG only."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis from EC2"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }

  tags = { Name = "${var.project}-cache" }
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/security.tf
git commit -m "Add security groups: web 80/443, db/cache restricted to web SG"
```

---

### Task 6: ECR + asset bucket for compose/Caddyfile

**Files:**
- Create: `infra/ecr.tf`
- Create: `infra/assets.tf`

**Interfaces:**
- Produces: `aws_ecr_repository.app` (image registry); `aws_s3_bucket.assets` holding `docker-compose.prod.yml` + `Caddyfile` for the instance to download at boot.

- [ ] **Step 1: Create `infra/ecr.tf`**

```hcl
resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
```

- [ ] **Step 2: Create `infra/assets.tf`**

```hcl
resource "aws_s3_bucket" "assets" {
  bucket = "${var.project}-assets-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "compose" {
  bucket = aws_s3_bucket.assets.id
  key    = "docker-compose.prod.yml"
  source = "${path.module}/../docker-compose.prod.yml"
  etag   = filemd5("${path.module}/../docker-compose.prod.yml")
}

resource "aws_s3_object" "caddyfile" {
  bucket = aws_s3_bucket.assets.id
  key    = "Caddyfile"
  source = "${path.module}/../Caddyfile"
  etag   = filemd5("${path.module}/../Caddyfile")
}
```

- [ ] **Step 3: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 4: Commit**

```bash
git add infra/ecr.tf infra/assets.tf
git commit -m "Add ECR repo (lifecycle) and S3 asset bucket for compose/Caddyfile"
```

---

### Task 7: SSM parameters + generated secrets

**Files:**
- Create: `infra/ssm.tf`

**Interfaces:**
- Produces: SecureString params under `/deal-hunter/prod/*`. `JWT_SECRET` and `DB_PASSWORD` hold real generated values (consumed by RDS in Task 8 and the instance at boot). The rest are created with placeholder values the user overwrites out-of-band; Terraform ignores later value drift.
- Produces: `random_password.db` (used by `aws_db_instance` in Task 8).

- [ ] **Step 1: Create `infra/ssm.tf`**

```hcl
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
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/ssm.tf
git commit -m "Add SSM SecureString params + generated DB/JWT secrets"
```

---

### Task 8: RDS Postgres

**Files:**
- Create: `infra/rds.tf`

**Interfaces:**
- Consumes: `aws_subnet.private`, `aws_security_group.db`, `random_password.db`, `var.db_name`, `var.db_username`.
- Produces: `aws_db_instance.pg` with attribute `address` (host) used to build `DATABASE_URL` at boot.

- [ ] **Step 1: Create `infra/rds.tf`**

```hcl
resource "aws_db_subnet_group" "pg" {
  name       = "${var.project}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "pg" {
  identifier             = "${var.project}-pg"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.db_instance_class
  allocated_storage      = 20
  storage_type           = "gp2"
  db_name                = var.db_name
  username               = var.db_username
  password               = random_password.db.result
  db_subnet_group_name   = aws_db_subnet_group.pg.name
  vpc_security_group_ids = [aws_security_group.db.id]
  multi_az               = false
  publicly_accessible    = false
  skip_final_snapshot    = true
  deletion_protection    = false
  apply_immediately      = true
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/rds.tf
git commit -m "Add RDS Postgres (db.t3.micro, private, single-AZ)"
```

---

### Task 9: ElastiCache Redis

**Files:**
- Create: `infra/elasticache.tf`

**Interfaces:**
- Consumes: `aws_subnet.private`, `aws_security_group.cache`, `var.cache_node_type`.
- Produces: `aws_elasticache_cluster.redis` with `cache_nodes[0].address` used to build `REDIS_URL` at boot.

- [ ] **Step 1: Create `infra/elasticache.tf`**

```hcl
resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project}-cache"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id         = "${var.project}-redis"
  engine             = "redis"
  node_type          = var.cache_node_type
  num_cache_nodes    = 1
  parameter_group_name = "default.redis7"
  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.cache.id]
  port               = 6379
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/elasticache.tf
git commit -m "Add ElastiCache Redis (cache.t3.micro, single node, private)"
```

---

### Task 10: GitHub OIDC provider + deploy role

**Files:**
- Create: `infra/iam_github_oidc.tf`

**Interfaces:**
- Consumes: `var.github_repo`, `aws_ecr_repository.app`.
- Produces: `aws_iam_role.github` (assumed by Actions via OIDC) with ECR push + `ssm:SendCommand` permissions. Its ARN is exported in Task 13's outputs.

- [ ] **Step 1: Create `infra/iam_github_oidc.tf`**

```hcl
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github" {
  name               = "${var.project}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

data "aws_iam_policy_document" "github_perms" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    sid       = "SsmDeploy"
    actions   = ["ssm:SendCommand"]
    resources = ["*"]
  }
  statement {
    sid       = "SsmReadCommand"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github" {
  name   = "${var.project}-github-deploy"
  role   = aws_iam_role.github.id
  policy = data.aws_iam_policy_document.github_perms.json
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/iam_github_oidc.tf
git commit -m "Add GitHub OIDC provider + deploy role (ECR push, SSM send)"
```

---

### Task 11: EC2 instance, instance role, EIP, and user_data

**Files:**
- Create: `infra/ec2.tf`
- Create: `infra/user_data.sh`

**Interfaces:**
- Consumes: subnets, `aws_security_group.web`, AMI data source, `aws_db_instance.pg`, `aws_elasticache_cluster.redis`, `aws_ecr_repository.app`, `aws_s3_bucket.assets`, `var.*`.
- Produces: `aws_instance.web` (tag `Role=deal-hunter` for SSM targeting), `aws_eip.web` (public IP → sslip.io hostname).

- [ ] **Step 1: Create `infra/ec2.tf`**

```hcl
resource "aws_eip" "web" {
  domain = "vpc"
  tags   = { Name = "${var.project}-eip" }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.project}-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

data "aws_iam_policy_document" "ec2_inline" {
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/prod/*"]
  }
  statement {
    sid       = "ReadAssets"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.assets.arn}/*"]
  }
}

resource "aws_iam_role_policy" "ec2_inline" {
  name   = "${var.project}-ec2-inline"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2_inline.json
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project}-ec2"
  role = aws_iam_role.ec2.name
}

resource "aws_instance" "web" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  user_data = templatefile("${path.module}/user_data.sh", {
    region         = var.region
    project        = var.project
    ecr_repo_url   = aws_ecr_repository.app.repository_url
    assets_bucket  = aws_s3_bucket.assets.id
    db_host        = aws_db_instance.pg.address
    db_name        = var.db_name
    db_username    = var.db_username
    redis_host     = aws_elasticache_cluster.redis.cache_nodes[0].address
    site_address   = "https://${var.project}.${replace(aws_eip.web.public_ip, ".", "-")}.sslip.io"
  })

  tags = {
    Name = "${var.project}-web"
    Role = var.project
  }
}

resource "aws_eip_association" "web" {
  instance_id   = aws_instance.web.id
  allocation_id = aws_eip.web.id
}
```

- [ ] **Step 2: Create `infra/user_data.sh`**

```bash
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
```

- [ ] **Step 3: Validate (terraform + shell syntax)**

Run:
```bash
cd infra && terraform fmt -check && terraform validate && cd ..
bash -n infra/user_data.sh && echo "shell OK"
```
Note: `bash -n` on the template will flag `${region}`-style tokens as valid bash `${var}` expansion, which parses fine. Expected: terraform valid + `shell OK`.

- [ ] **Step 4: Commit**

```bash
git add infra/ec2.tf infra/user_data.sh
git commit -m "Add EC2 instance, instance role/profile, EIP, and boot user_data"
```

---

### Task 12: Budget alarm + outputs

**Files:**
- Create: `infra/budgets.tf`
- Create: `infra/outputs.tf`

**Interfaces:**
- Consumes: `var.budget_alert_email`, `aws_eip.web`, `aws_ecr_repository.app`, `aws_instance.web`, `aws_iam_role.github`.
- Produces: outputs `site_url`, `ecr_repository_url`, `ecr_repository_name`, `instance_id`, `github_role_arn`, `region`, `account_id` (consumed by the runbook + GitHub repo variables).

- [ ] **Step 1: Create `infra/budgets.tf`**

```hcl
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = "1.0"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
```

- [ ] **Step 2: Create `infra/outputs.tf`**

```hcl
output "site_url" {
  value = "https://${var.project}.${replace(aws_eip.web.public_ip, ".", "-")}.sslip.io"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.app.name
}

output "instance_id" {
  value = aws_instance.web.id
}

output "github_role_arn" {
  value = aws_iam_role.github.arn
}

output "region" {
  value = var.region
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}
```

- [ ] **Step 3: Validate**

Run: `cd infra && terraform fmt -check && terraform validate && cd ..`
Expected: valid.

- [ ] **Step 4: Commit**

```bash
git add infra/budgets.tf infra/outputs.tf
git commit -m "Add $1 budget alarm and Terraform outputs"
```

---

### Task 13: Secrets helper script

**Files:**
- Create: `scripts/put-secrets.sh`

**Interfaces:**
- Consumes: local env vars / `.env`; writes SSM SecureString values for the user-supplied secret names.

- [ ] **Step 1: Create `scripts/put-secrets.sh`**

```bash
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
```

- [ ] **Step 2: Validate**

Run: `bash -n scripts/put-secrets.sh && echo "shell OK"`
Expected: `shell OK`.

- [ ] **Step 3: Commit**

```bash
git add scripts/put-secrets.sh
git commit -m "Add helper to load app secrets into SSM Parameter Store"
```

---

### Task 14: CI workflow (tests + migration check)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a `tests` job (sqlite + fakeredis, fast) and a `migrations` job (real Postgres service, `alembic upgrade head`). Both are required checks before deploy.

- [ ] **Step 1: Create `.github/workflows/ci.yml`** (note: in the `migrations` job, `services:` and `steps:` are siblings)

```yaml
name: ci

on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Run test suite (SQLite + fakeredis)
        run: pytest -q

  migrations:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: deals
          POSTGRES_PASSWORD: deals
          POSTGRES_DB: deals
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U deals"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Verify migrations apply on real Postgres
        env:
          DATABASE_URL: postgresql+psycopg://deals:deals@localhost:5432/deals
        run: alembic upgrade head
```

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "yaml OK"`
Expected: `yaml OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Add CI: pytest (sqlite/fakeredis) + Alembic migration check"
```

---

### Task 15: Deploy workflow (OIDC → ECR → SSM)

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes GitHub repo variables: `AWS_REGION`, `AWS_ACCOUNT_ID`, `AWS_ROLE_ARN`, `ECR_REPOSITORY`, `INSTANCE_ID`, `SITE_URL` (set from Terraform outputs).
- Produces: image push to ECR + SSM deploy + `/health` smoke test.

- [ ] **Step 1: Create `.github/workflows/deploy.yml`**

```yaml
name: deploy

on:
  push:
    branches: ["main"]

permissions:
  id-token: write
  contents: read

concurrency:
  group: deploy-main
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}

      - name: Login to ECR
        id: ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push image
        env:
          REGISTRY: ${{ steps.ecr.outputs.registry }}
          REPO: ${{ vars.ECR_REPOSITORY }}
          SHA: ${{ github.sha }}
        run: |
          docker build -t "$REGISTRY/$REPO:$SHA" -t "$REGISTRY/$REPO:latest" .
          docker push "$REGISTRY/$REPO:$SHA"
          docker push "$REGISTRY/$REPO:latest"

      - name: Deploy on EC2 via SSM
        env:
          INSTANCE_ID: ${{ vars.INSTANCE_ID }}
          REGION: ${{ vars.AWS_REGION }}
          REGISTRY: ${{ steps.ecr.outputs.registry }}
        run: |
          CMD_ID=$(aws ssm send-command \
            --region "$REGION" \
            --instance-ids "$INSTANCE_ID" \
            --document-name "AWS-RunShellScript" \
            --comment "deploy ${{ github.sha }}" \
            --parameters "{\"workingDirectory\":[\"/opt/deal-hunter\"],\"commands\":[\"set -e\",\"aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REGISTRY\",\"docker compose -f docker-compose.prod.yml pull\",\"docker compose -f docker-compose.prod.yml run --rm web alembic upgrade head\",\"docker compose -f docker-compose.prod.yml up -d\"]}" \
            --query "Command.CommandId" --output text)
          echo "SSM command: $CMD_ID"
          aws ssm wait command-executed --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" || true
          STATUS=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query Status --output text)
          echo "Deploy status: $STATUS"
          test "$STATUS" = "Success"

      - name: Smoke test /health
        env:
          SITE_URL: ${{ vars.SITE_URL }}
        run: |
          for i in $(seq 1 10); do
            if curl -fsS "$SITE_URL/health" | grep -q '"status":"ok"'; then echo "healthy"; exit 0; fi
            sleep 15
          done
          echo "health check failed"; exit 1
```

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo "yaml OK"`
Expected: `yaml OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "Add deploy workflow: OIDC build+push to ECR, SSM deploy, smoke test"
```

---

### Task 16: Hands-on runbook

**Files:**
- Create: `docs/aws-deploy-runbook.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Create `docs/aws-deploy-runbook.md`**

````markdown
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
````

- [ ] **Step 2: Commit**

```bash
git add docs/aws-deploy-runbook.md
git commit -m "Add AWS deploy runbook (bootstrap, apply, secrets, first deploy, teardown)"
```

---

## Self-Review Notes

- **Spec §8 correction:** the spec described running pytest against Postgres+Redis service containers. The suite actually uses in-memory SQLite + fakeredis (per `tests/conftest.py`), so CI runs `pytest` directly (fast job) plus a *separate* `migrations` job that stands up real Postgres to verify Alembic — simpler and stronger. Implemented in Task 14.
- **Spec coverage:** network/no-NAT (T4), compute+swap (T11), RDS (T8), ElastiCache (T9), no-SSH/SSM (T11 role + runbook), ECR (T6), prod compose+Caddy/sslip (T1, T11), SSM secrets (T7,T13), IAM instance + OIDC (T10,T11), CI/CD (T14,T15), S3+DynamoDB state (T2), budget+teardown (T12,T16). All covered.
- **Free-tier guardrails:** no NAT, no ALB, single micro instances, lifecycle-pruned ECR, $1 budget — all present.
- **Ordering note:** file-creation order here is logical, not apply order. Terraform resolves the real dependency graph; the runbook (T16) gives the human apply order.
