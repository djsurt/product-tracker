# AWS Project Interview Quiz Artifact — Design

**Date:** 2026-07-28  
**Status:** Approved conversationally; awaiting revised written-spec review

## Goal

Create a reusable, standalone HTML quiz that tests mid-level AWS knowledge
through interview questions grounded in the Deal Hunter project's implemented
infrastructure and deployment workflow. The artifact should help the user
identify knowledge gaps, not merely test AWS vocabulary.

## Delivery

The artifact will be one self-contained HTML file with embedded CSS and
JavaScript. It will:

- Work offline by opening the file directly in a browser.
- Require no installation, build process, server, or external dependency.
- Keep all quiz content and scoring logic local.
- Work on desktop and mobile layouts.

It will not be added as a route in the existing FastAPI application because the
quiz is a reusable study aid rather than a production feature.

## Interview Flow

1. A welcome screen explains the format, scoring, and expected answer length.
2. The artifact presents 12 primary questions, one at a time.
3. The user writes a free-text answer and selects a confidence level.
4. An optional hint may be revealed before submitting an answer.
5. On submission, the artifact reveals:
   - A model interview answer.
   - A checklist of expected key points.
   - A concise interviewer explanation and likely follow-up question.
6. The user marks each expected key point they covered.
7. The artifact calculates the question score and allows progression.
8. The results screen shows the overall score, category breakdown, confidence
   calibration, strengths, weak topics, and recommended review actions.
9. The user can restart the full interview or retry only weak questions.

Answers remain editable until the user submits the question. Once the model
answer is revealed, the answer is locked for that attempt so the assessment
reflects the user's original knowledge.

## Question Coverage

Questions will draw from the repository's actual implementation:

1. End-to-end architecture and request/data flow.
2. VPC, public/private subnet placement, routing, and the no-NAT choice.
3. Security-group relationships for EC2, RDS PostgreSQL, and ElastiCache Redis.
4. EC2 workload layout: Caddy, FastAPI, Celery worker, and Celery beat.
5. IAM roles, instance profiles, least privilege, and the absence of SSH.
6. GitHub Actions OIDC trust and short-lived AWS credentials.
7. ECR image tags, image lifecycle, and deployment consistency.
8. Parameter Store secrets and SSM Run Command deployment.
9. Terraform remote state in S3 and state locking with DynamoDB.
10. Migrations, deploy concurrency, health checks, and rollback behavior.
11. Free-tier decisions, including avoiding a NAT Gateway and ALB.
12. A production incident and hardening scenario spanning multiple services.

The questions will be distributed across five result categories:

- Architecture
- Networking and security
- IAM and CI/CD
- Operations
- Cost and reliability

## Difficulty and Answer Quality

Questions target a mid-level engineer. Strong answers should:

- Describe this project's implementation rather than only define AWS services.
- Explain why a choice was made and identify its downside.
- Trace dependencies and failure modes across services.
- Distinguish the cost-optimized demo topology from a highly available
  production topology.
- Suggest proportionate improvements without redesigning the entire system.

## Scoring

Static JavaScript cannot reliably judge the meaning of unrestricted prose, so
the artifact will use guided self-assessment rather than pretend to perform
automatic semantic grading.

Each question is worth five points:

- Four points from question-specific key points the user confirms they covered.
- One point for completing a substantive written answer before revealing the
  model response.

The maximum score is 60. Category scores use the same earned-versus-available
calculation for questions assigned to that category.

Confidence is recorded separately as low, medium, or high. The results screen
will highlight high-confidence weak answers and low-confidence strong answers
as calibration signals; confidence will not inflate the knowledge score.

A weak question is one scoring below three out of five. “Retry weak areas”
creates a new attempt containing only those questions and resets their answers,
checks, hints, and confidence.

## Visual Design

The interface will resemble a focused technical interview rather than a
game-show quiz:

- A restrained AWS-inspired dark navy and warm orange palette.
- A progress indicator and question-category label.
- A large, readable question and distraction-free answer area.
- Clearly separated hint, model answer, checklist, and feedback panels.
- Accessible contrast, visible keyboard focus, semantic controls, and reduced
  motion support.
- Responsive behavior without horizontal scrolling at narrow widths.

## State and Privacy

Quiz state will live in browser memory only. Restarting or reopening the file
starts a fresh interview. The artifact will not transmit, persist, or log the
user's answers.

## Validation

Before delivery:

- Verify all 12 questions and explanations against the Terraform, workflows,
  production Compose file, user-data script, and AWS runbook.
- Exercise the initial, answering, revealed-answer, final-results, restart, and
  retry-weak-areas states.
- Confirm score totals and category calculations with known selections.
- Check the artifact at desktop and mobile viewport widths.
- Confirm it opens and functions directly from the filesystem with no network.
- Check keyboard navigation, visible focus, and reduced-motion behavior.
