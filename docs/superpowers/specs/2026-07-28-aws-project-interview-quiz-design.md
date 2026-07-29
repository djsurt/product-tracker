# AWS Project Interview Quiz — Design

**Date:** 2026-07-28  
**Status:** Approved conversationally; awaiting written-spec review

## Goal

Run an interactive, mid-level AWS mock interview grounded in the Deal Hunter
project's implemented infrastructure and deployment workflow. The interview
should test whether the candidate can explain the system, defend its trade-offs,
and diagnose realistic failures rather than merely recall AWS definitions.

## Format

- Ask 12 primary questions, one at a time.
- Stay in interviewer voice while questions and follow-ups are active.
- Use follow-up questions when an answer is incomplete, exposes an important
  trade-off, or merits deeper investigation.
- After each primary question and its follow-ups, give concise feedback and a
  score before moving on.
- Let the candidate ask for clarification as they would in a real interview.
- Finish with an overall assessment, strengths, gaps, and targeted review topics.

## Coverage

The questions will draw from the repository's actual implementation:

1. End-to-end AWS architecture and request/data flow.
2. VPC, public and private subnet placement, routing, and the no-NAT design.
3. Security groups for EC2, RDS PostgreSQL, and ElastiCache Redis.
4. EC2 workload layout: Caddy, FastAPI, Celery worker, and Celery beat.
5. IAM roles, instance profiles, least privilege, and the absence of SSH.
6. GitHub Actions OIDC trust and short-lived AWS credentials.
7. ECR image tagging, lifecycle management, and deployment behavior.
8. Systems Manager Parameter Store secrets and SSM Run Command deployment.
9. Terraform remote state in S3 and locking with DynamoDB.
10. CI/CD migrations, deploy concurrency, health checks, and rollback concerns.
11. Free-tier cost decisions, including avoiding NAT Gateway and an ALB.
12. Failure diagnosis and production-hardening trade-offs.

## Difficulty

Questions target a mid-level engineer. Strong answers should:

- Describe what this project does specifically, not just define AWS services.
- Explain why a design choice was made and identify its downside.
- Trace dependencies and failure modes across services.
- Recognize where the cost-optimized demo architecture differs from a
  highly-available production architecture.
- Suggest proportionate improvements without redesigning the entire system.

## Scoring

Each primary question is scored from 0–5:

- **Accuracy (0–2):** Correctly explains the implementation and AWS concepts.
- **Depth (0–2):** Covers rationale, trade-offs, security, or failure modes.
- **Communication (0–1):** Gives a structured, direct interviewer-style answer.

Final score: 60 points. The closing assessment will report both the score and
qualitative readiness; follow-up difficulty will not reduce the score merely
because the interviewer probes beyond the expected mid-level answer.

## Interviewer Behavior

The interviewer will not reveal the model answer before the candidate responds.
Feedback will distinguish factual errors from reasonable design alternatives.
If the repository contains a questionable implementation choice, the interviewer
will treat recognizing and improving it as a strength rather than requiring the
candidate to defend it blindly.
