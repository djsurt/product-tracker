# AWS Project Interview Quiz Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable, offline, self-contained HTML artifact that tests mid-level AWS knowledge using 12 interview questions specific to this repository.

**Architecture:** Create one standalone `artifacts/aws-project-interview-quiz.html` file containing semantic HTML, embedded CSS, quiz data, and vanilla JavaScript. A focused pytest contract test will parse the artifact with the Python standard library and validate its content, scoring inputs, privacy constraints, and required UI hooks; browser validation will exercise behavior and responsive presentation.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, Python 3 standard library, pytest, Codex in-app browser

## Global Constraints

- The artifact must open directly from the filesystem and make no network requests.
- It must require no build process, server, external dependency, or installation.
- It must contain exactly 12 project-specific mid-level AWS questions.
- Each question is worth five points: four self-assessed key points and one point for a substantive written answer.
- Confidence is low, medium, or high and does not change the knowledge score.
- Questions scoring below three out of five are weak and eligible for a focused retry.
- State remains in browser memory and is neither persisted nor transmitted.
- The interface must work on desktop and mobile without horizontal scrolling.
- Keyboard focus, semantic controls, accessible contrast, and reduced-motion behavior are required.

---

### Task 1: Artifact Contract and Project-Specific Quiz Content

**Files:**
- Create: `tests/test_aws_quiz_artifact.py`
- Create: `artifacts/aws-project-interview-quiz.html`

**Interfaces:**
- Consumes: Terraform and deployment facts from `infra/*.tf`, `infra/user_data.sh`, `.github/workflows/deploy.yml`, `docker-compose.prod.yml`, and `docs/aws-deploy-runbook.md`.
- Produces: A `<script id="quiz-data" type="application/json">` element containing an array of question objects with the fields `id`, `category`, `question`, `hint`, `modelAnswer`, `keyPoints`, `followUp`, and `review`.

- [ ] **Step 1: Write the failing artifact contract tests**

Create `tests/test_aws_quiz_artifact.py` with standard-library parsing helpers and
tests that enforce the artifact's offline and content contracts:

```python
import json
from html.parser import HTMLParser
from pathlib import Path


ARTIFACT = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "aws-project-interview-quiz.html"
)


class QuizParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_quiz_data = False
        self.quiz_data = []
        self.ids = set()
        self.external_urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "script" and values.get("id") == "quiz-data":
            self.in_quiz_data = True
        for name in ("src", "href"):
            value = values.get(name, "")
            if value.startswith(("http://", "https://", "//")):
                self.external_urls.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_quiz_data:
            self.in_quiz_data = False

    def handle_data(self, data):
        if self.in_quiz_data:
            self.quiz_data.append(data)


def parse_artifact():
    parser = QuizParser()
    parser.feed(ARTIFACT.read_text(encoding="utf-8"))
    return parser, json.loads("".join(parser.quiz_data))


def test_artifact_is_self_contained_and_has_required_screens():
    parser, _ = parse_artifact()
    assert parser.external_urls == []
    assert {
        "welcome-screen",
        "question-screen",
        "results-screen",
        "answer-input",
        "confidence-group",
        "hint-panel",
        "model-answer",
        "key-points",
        "category-results",
    } <= parser.ids


def test_quiz_has_twelve_complete_project_specific_questions():
    _, questions = parse_artifact()
    assert len(questions) == 12
    assert [question["id"] for question in questions] == list(range(1, 13))
    required = {
        "id",
        "category",
        "question",
        "hint",
        "modelAnswer",
        "keyPoints",
        "followUp",
        "review",
    }
    assert all(required == set(question) for question in questions)
    assert all(len(question["keyPoints"]) == 4 for question in questions)
    combined = json.dumps(questions).lower()
    for project_term in (
        "10.20.0.0/16",
        "caddy",
        "celery",
        "oidc",
        "ecr",
        "ssm",
        "dynamodb",
        "nat gateway",
        "elasticache",
    ):
        assert project_term in combined


def test_questions_cover_all_result_categories():
    _, questions = parse_artifact()
    assert {question["category"] for question in questions} == {
        "Architecture",
        "Networking & Security",
        "IAM & CI/CD",
        "Operations",
        "Cost & Reliability",
    }
```

- [ ] **Step 2: Run the contract tests and verify they fail**

Run:

```bash
pytest tests/test_aws_quiz_artifact.py -v
```

Expected: FAIL because `artifacts/aws-project-interview-quiz.html` does not exist.

- [ ] **Step 3: Create the semantic artifact shell and quiz dataset**

Create `artifacts/aws-project-interview-quiz.html`. Include:

- Three top-level sections with IDs `welcome-screen`, `question-screen`, and
  `results-screen`.
- A progress bar, category badge, question heading, `textarea#answer-input`,
  a `fieldset#confidence-group` with Low/Medium/High radio controls, hint button
  and `aside#hint-panel`, submit button, `section#model-answer`,
  `fieldset#key-points`, score feedback, and next-question button.
- Results containers for overall score, `#category-results`, confidence
  calibration, strengths, weak topics, and review recommendations.
- Restart and retry-weak-area buttons.
- A JSON dataset with these exact 12 focuses:
  1. Trace a public request through the EIP, Caddy, FastAPI, PostgreSQL/Redis,
     Celery workers, and external marketplace APIs.
  2. Explain `10.20.0.0/16`, public/private subnet placement, the Internet
     Gateway route, and why no NAT Gateway exists.
  3. Explain security-group references for ports 80/443, 5432, and 6379 and why
     PostgreSQL and Redis are not internet reachable.
  4. Explain why web, worker, beat, and Caddy share one EC2 host and the
     reliability/scale consequences.
  5. Explain the EC2 instance profile, SSM management without port 22, ECR read
     access, Parameter Store path restriction, and S3 asset read access.
  6. Explain GitHub OIDC `AssumeRoleWithWebIdentity`, audience/subject
     conditions, branch restriction, and short-lived credentials.
  7. Explain ECR SHA and `latest` tags, lifecycle pruning, and the deployment
     consistency risk of Compose pulling `latest`.
  8. Explain generated and user-provided SecureString parameters, boot-time
     `.env` creation, and SSM Run Command deployment.
  9. Explain why bootstrap state uses versioned/encrypted/private S3 plus
     DynamoDB locking and why bootstrap must precede the main stack.
  10. Explain migration-before-restart, deploy concurrency, command-status
      checking, smoke testing, and the current lack of automated rollback.
  11. Defend avoiding NAT Gateway and ALB for cost, then explain when production
      requirements would justify adding them or alternatives.
  12. Diagnose a healthy EC2 host with a failing `/health` after deploy by
      checking SSM output, containers, migration status, RDS/Redis reachability,
      security groups, secrets, and image version.
- Four concise, independently checkable key points for every question.
- A model answer that states the repository-specific facts, their rationale, and
  at least one trade-off.
- A likely interviewer follow-up and a concrete review recommendation.

- [ ] **Step 4: Run the contract tests and verify they pass**

Run:

```bash
pytest tests/test_aws_quiz_artifact.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit the content contract and artifact dataset**

```bash
git add tests/test_aws_quiz_artifact.py artifacts/aws-project-interview-quiz.html
git commit -m "Add project-specific AWS interview quiz content"
```

---

### Task 2: Interactive Assessment, Scoring, and Responsive Presentation

**Files:**
- Modify: `tests/test_aws_quiz_artifact.py`
- Modify: `artifacts/aws-project-interview-quiz.html`

**Interfaces:**
- Consumes: The question schema and semantic hooks from Task 1.
- Produces: Browser functions `startQuiz(questionIds)`, `renderQuestion()`,
  `revealAnswer()`, `calculateQuestionScore()`, `showResults()`,
  `retryWeakQuestions()`, and `restartQuiz()`.

- [ ] **Step 1: Add failing interaction-contract tests**

Append:

```python
def test_interaction_and_scoring_contract_is_embedded():
    html = ARTIFACT.read_text(encoding="utf-8")
    for function_name in (
        "startQuiz",
        "renderQuestion",
        "revealAnswer",
        "calculateQuestionScore",
        "showResults",
        "retryWeakQuestions",
        "restartQuiz",
    ):
        assert f"function {function_name}(" in html
    assert "answer.trim().length >= 40" in html
    assert "score < 3" in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html


def test_accessibility_and_responsive_contract_is_embedded():
    html = ARTIFACT.read_text(encoding="utf-8")
    assert 'aria-live="polite"' in html
    assert ":focus-visible" in html
    assert "@media (max-width: 720px)" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
```

- [ ] **Step 2: Run the tests and verify the new assertions fail**

Run:

```bash
pytest tests/test_aws_quiz_artifact.py -v
```

Expected: Existing content tests PASS; the new interaction/accessibility tests
FAIL because the named functions and complete styles do not exist.

- [ ] **Step 3: Implement in-memory interview state and transitions**

In the artifact's embedded JavaScript:

- Parse `#quiz-data` once and keep `activeQuestionIds`, `currentIndex`,
  `responses`, and `revealed` in memory.
- `startQuiz(questionIds)` resets state and displays the first requested
  question.
- Require a confidence selection and a non-empty answer before reveal; show an
  inline validation message in an `aria-live="polite"` region.
- Award the written-answer point only when `answer.trim().length >= 40`.
- Lock the textarea and confidence controls after `revealAnswer()`.
- Render four checkboxes from `keyPoints`; update the displayed score as the
  user checks them.
- Store answer, confidence, hint use, checked key-point indexes, and score per
  question.
- Treat `score < 3` as weak.
- On completion, calculate total/category percentages and confidence
  calibration without adding confidence to the score.
- `retryWeakQuestions()` starts a clean attempt using only weak question IDs.
- `restartQuiz()` starts a clean 12-question attempt.

- [ ] **Step 4: Implement the visual system and responsive/accessibility states**

In embedded CSS:

- Define navy, slate, cream, and warm-orange custom properties.
- Use a constrained reading column, a strong typographic hierarchy, and a
  sticky/visible progress header without obscuring content.
- Distinguish question, hint, model answer, checklist, and feedback panels by
  structure and contrast, not color alone.
- Add `:focus-visible` outlines to every interactive control.
- At `max-width: 720px`, stack controls, use full-width action buttons, and
  prevent horizontal overflow.
- Under `prefers-reduced-motion: reduce`, disable transitions and smooth scroll.

In embedded HTML/JavaScript:

- Use buttons for actions and fieldsets/legends for radio and checkbox groups.
- Move focus to the question heading after navigation and to the model-answer
  heading after reveal.
- Announce validation, question score changes, and result completion through
  the polite live region.

- [ ] **Step 5: Run automated tests**

Run:

```bash
pytest tests/test_aws_quiz_artifact.py -v
pytest -q
```

Expected: Artifact tests PASS and the existing project suite remains green.

- [ ] **Step 6: Perform browser behavior and visual validation**

Open the artifact directly using a `file://` URL and verify:

1. Welcome → start → answer/confidence → reveal → checklist → next.
2. Empty answer and missing confidence validation.
3. Hint reveal without model-answer leakage.
4. Score of 1/5 with no key points and 5/5 with all four.
5. Final score and all five category rows.
6. High-confidence weak and low-confidence strong calibration messages.
7. Retry weak areas resets only weak questions.
8. Restart returns to a clean 12-question attempt.
9. Desktop and 390-pixel-wide mobile layouts have no clipped content or
   horizontal scroll.
10. Keyboard-only navigation has visible focus and logical focus movement.

Expected: All behaviors work with no console errors and no network activity.

- [ ] **Step 7: Commit the finished artifact**

```bash
git add tests/test_aws_quiz_artifact.py artifacts/aws-project-interview-quiz.html
git commit -m "Complete interactive AWS interview quiz artifact"
```

---

## Plan Self-Review

- Spec coverage: delivery, 12-question content, scoring, confidence calibration,
  weak-area retry, visual design, privacy, accessibility, mobile layout, and
  offline validation are mapped to Tasks 1–2.
- Placeholder scan: no deferred content or unspecified implementation steps.
- Interface consistency: the Task 2 function names, HTML IDs, question fields,
  score threshold, and answer-length rule match the Task 1 contract and global
  constraints.
