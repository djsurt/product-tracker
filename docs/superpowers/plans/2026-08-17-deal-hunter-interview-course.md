# Deal Hunter Interview Course Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished standalone HTML course that teaches the Deal Hunter project, connects it to new-grad software-engineering fundamentals, saves local progress, and supports active interview practice.

**Architecture:** Create one offline-first artifact containing semantic course content, embedded CSS, inline diagrams, and progressive vanilla-JavaScript interactions. A focused Python contract test parses the HTML and validates content, privacy, accessibility, responsive styling, and interaction hooks; browser QA verifies the behavior and visual result.

**Tech Stack:** HTML5, embedded CSS, vanilla JavaScript, inline SVG/semantic HTML diagrams, Python standard-library `html.parser`, pytest, Codex browser automation.

## Global Constraints

- Create exactly one runtime artifact: `artifacts/deal-hunter-interview-course.html`.
- Require no server, build step, package installation, or network connection.
- Do not load external fonts, scripts, images, stylesheets, analytics, or APIs.
- Use `localStorage` only under the key `deal-hunter-interview-course:v1`.
- Store only `version`, `completedModules`, and `openDetails`; never persist knowledge-check answers.
- All instructional content must remain readable when JavaScript is unavailable.
- Support keyboard navigation, `prefers-reduced-motion`, widths up to and below 760px, and printing.
- Do not invent usage, performance, revenue, user, or team-contribution claims.
- Distinguish current repository behavior from proposed future improvements.
- Use a deep-navy, warm-cream, restrained-orange, slate, and accessible-green palette with system fonts only.
- Treat the existing AWS quiz as optional; do not link to it unless its artifact exists on the implementation branch at a known relative path.

---

## File structure

- `artifacts/deal-hunter-interview-course.html` — the complete portable course: semantic content, diagrams, CSS, and progressive JavaScript.
- `tests/test_interview_course_artifact.py` — static contract tests for the course's content, document structure, offline/privacy properties, accessibility hooks, styles, and JavaScript interfaces.

The single-file artifact is deliberate: portability is a product requirement. Internal HTML sections remain bounded by module IDs and shared class/interface conventions so content, styling, and interaction logic stay understandable despite sharing one physical file.

---

### Task 1: Semantic Course Shell and Repository-Grounded Curriculum

**Files:**
- Create: `tests/test_interview_course_artifact.py`
- Create: `artifacts/deal-hunter-interview-course.html`

**Interfaces:**
- Consumes: Repository facts from `README.md`, `core/models.py`, `workers/pipeline.py`, `workers/tasks.py`, `sources/base.py`, `sources/registry.py`, `api/mcp_server.py`, `api/mcp_service.py`, `api/routers/web.py`, `docker-compose.prod.yml`, `.github/workflows/*.yml`, and `infra/*.tf`.
- Produces: Eleven `<article class="course-module" id="module-…" data-module-id="…" data-minutes="…">` elements; stable module IDs consumed by navigation and JavaScript in later tasks.

- [ ] **Step 1: Write the failing content and offline-contract tests**

Create `tests/test_interview_course_artifact.py`:

```python
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "deal-hunter-interview-course.html"

MODULE_IDS = [
    "project-story",
    "system-architecture",
    "database-modeling",
    "async-redis",
    "reliability",
    "api-security-ui",
    "ai-mcp",
    "aws-cicd",
    "scaling",
    "new-grad-fundamentals",
    "interview-practice",
]


class CourseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.module_ids: list[str] = []
        self.external_resources: list[tuple[str, str]] = []
        self.form_actions: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if element_id := values.get("id"):
            self.ids.add(element_id)
        if tag == "article" and "course-module" in values.get("class", "").split():
            self.module_ids.append(values.get("data-module-id", ""))
        for attribute in ("src", "href"):
            value = values.get(attribute, "")
            if value.startswith(("http://", "https://", "//")):
                self.external_resources.append((attribute, value))
        if tag == "form" and values.get("action"):
            self.form_actions.append(values["action"])

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def parse_course() -> tuple[CourseParser, str]:
    html = ARTIFACT.read_text(encoding="utf-8")
    parser = CourseParser()
    parser.feed(html)
    return parser, html


def test_course_has_complete_ordered_curriculum():
    parser, html = parse_course()
    assert parser.module_ids == MODULE_IDS
    assert len(parser.module_ids) == len(set(parser.module_ids)) == 11
    for number in range(1, 12):
        assert f'Module {number}' in html
    assert html.count('class="module-complete"') == 11


def test_course_covers_repository_specific_system():
    parser, _ = parse_course()
    text = " ".join(parser.text).lower()
    required = (
        "fastapi", "postgresql", "redis", "celery", "celery beat",
        "pricesource", "normalizedoffer", "pricepoint", "decimal",
        "at-least-once", "idempotency", "cache-aside", "server-sent events",
        "404", "410", "429", "dead letter", "mcp", "sha-256", "meshy",
        "terraform", "github oidc", "rds", "elasticache", "ecr", "ssm",
        "single point of failure", "currency normalization", "rollback",
    )
    for term in required:
        assert term in text


def test_course_contains_new_grad_and_interview_practice_content():
    parser, _ = parse_course()
    text = " ".join(parser.text).lower()
    for term in (
        "big o", "hash map", "index", "transaction", "dns", "tls",
        "process", "thread", "race condition", "cpu-bound", "i/o-bound",
        "star", "behavioral", "30-second", "60-second",
    ):
        assert term in text


def test_artifact_has_no_external_runtime_dependencies():
    parser, html = parse_course()
    assert parser.external_resources == []
    assert parser.form_actions == []
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "@import" not in html
    assert "analytics" not in html.lower()
```

- [ ] **Step 2: Run the tests and verify they fail because the artifact is absent**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: FAIL with `FileNotFoundError` for `artifacts/deal-hunter-interview-course.html`.

- [ ] **Step 3: Create the accessible static document shell**

Create the artifact with:

- `<!doctype html>`, `lang="en"`, UTF-8, viewport metadata, and a specific page title.
- A skip link targeting `#course-content`.
- `<header id="course-header">` containing the course title, two-to-three-hour expectation, project description, overall progress region, and controls with IDs `continue-course`, `print-course`, and `reset-progress`.
- `<nav id="desktop-syllabus" aria-label="Course syllabus">` and a mobile `<details id="mobile-syllabus">`, both containing normal `href="#module-…"` links for all eleven module IDs.
- `<main id="course-content">` containing the eleven articles in the exact `MODULE_IDS` order.
- `<footer id="course-finish">` containing a final readiness checklist and next-study recommendation.
- `<p id="course-live-region" class="sr-only" aria-live="polite"></p>`.
- A `<noscript>` notice explaining that lessons remain readable while progress is unavailable.
- Minimal embedded CSS sufficient to keep the raw document readable; Task 2 supplies the finished visual system.

Each module article must contain:

```html
<article class="course-module" id="module-project-story"
         data-module-id="project-story" data-minutes="12">
  <header class="module-header">
    <p class="module-kicker">Module 1 · 12 min</p>
    <h2>Tell the project story</h2>
    <p class="module-objective">After this module, you can…</p>
  </header>
  <!-- two to five lesson sections -->
  <section class="knowledge-check" data-check-id="project-story-check">…</section>
  <section class="module-summary">…</section>
  <button type="button" class="module-complete" data-module="project-story"
          aria-pressed="false">Mark module complete</button>
</article>
```

- [ ] **Step 4: Write all eleven modules with current facts, trade-offs, and practice**

Use concise prose and these exact content boundaries:

1. `project-story` (12 min): product problem, phased delivery, 30-second pitch,
   60-second pitch, contribution framing without invented team claims, and a
   “Why did you build it?” recall prompt.
2. `system-architecture` (16 min): component responsibility map; add-product
   flow; refresh flow; thin API rationale; `PriceSource`/`NormalizedOffer`;
   “trace one price update” recall prompt.
3. `database-modeling` (14 min): principal and supporting entities; UUID,
   server timestamp, `Numeric`/`Decimal`, unique constraint, history index,
   append-only source of truth versus `last_price` snapshot; current raw-currency
   comparison limitation.
4. `async-redis` (17 min): producer/broker/worker/Beat vocabulary;
   at-least-once semantics; Redis broker/cache/lock/rate-limit/metrics/pub-sub/
   quota roles; cache-aside; `SET NX EX`; blast-radius trade-off.
5. `reliability` (15 min): 404/410 delisting, 429 backpressure, transient
   exponential retry, exhausted-retry dead letter, stale-offer marking, partial
   discovery failure, alert debounce, and post-send commit behavior.
6. `api-security-ui` (15 min): FastAPI dependencies and sessions;
   authentication versus authorization; JWT browser session; bcrypt password
   hash versus SHA-256 API-token digest; owner-scoped not-found behavior;
   HTMX/Jinja; SSE versus polling/WebSockets.
7. `ai-mcp` (12 min): screenshot identification, config gates, MCP Streamable
   HTTP, the six tool names, per-user tool isolation, Meshy async generation,
   GLB/USDZ, monthly quota, cache-once, and filesystem scaling limitation.
8. `aws-cicd` (22 min): EIP/Caddy/FastAPI request path; public EC2 and private
   RDS/Redis; security-group references; no SSH; SSM; ECR; Parameter Store;
   S3/DynamoDB Terraform state; GitHub OIDC; migrations; health smoke test;
   no NAT/ALB rationale; `latest` consistency risk; no automated rollback.
9. `scaling` (15 min): measure queue depth, latency, DB load, Redis memory, and
   provider quotas; current single point of failure; 10x separation of web and
   workers; 100x ECS/ALB/autoscaling/Multi-AZ/S3; explain why microservices are
   not the first step.
10. `new-grad-fundamentals` (25 min): hash-map/list/set/queue/heap use and Big O;
    database indexes, joins, transactions, constraints, N+1; HTTP/DNS/TLS/status
    codes/timeouts; process/thread/race/lock/deadlock; CPU-bound versus I/O-bound;
    connect every subsection to a concrete Deal Hunter component.
11. `interview-practice` (20 min): fifteen technical prompts with native
    `<details>` model answers, five STAR story prompts, common overclaiming
    mistakes, and a readiness checklist covering pitch, flow, trade-off,
    limitation, failure story, and scaling answer.

Every module must include one `.knowledge-check`, one `.module-summary`, and one
`.module-complete` button. Use native `<details class="deep-dive">` for optional
depth and `<details class="model-answer">` for revealed interview answers so
content remains accessible without JavaScript.

- [ ] **Step 5: Run the focused tests and correct factual or structural misses**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Commit the curriculum foundation**

```bash
git add tests/test_interview_course_artifact.py artifacts/deal-hunter-interview-course.html
git commit -m "Add Deal Hunter interview course curriculum"
```

---

### Task 2: Editorial Visual System and Technical Diagrams

**Files:**
- Modify: `tests/test_interview_course_artifact.py`
- Modify: `artifacts/deal-hunter-interview-course.html`

**Interfaces:**
- Consumes: Stable module IDs and semantic sections from Task 1.
- Produces: CSS classes and visual IDs used by Task 3 and browser QA: `course-shell`, `course-sidebar`, `lesson-stack`, `architecture-visual`, `refresh-visual`, `entity-visual`, `failure-visual`, `aws-visual`, and `scaling-visual`.

- [ ] **Step 1: Add failing visual, accessibility, and responsive contract tests**

Append:

```python
def test_required_visuals_and_text_alternatives_exist():
    parser, html = parse_course()
    for visual_id in (
        "architecture-visual", "refresh-visual", "entity-visual",
        "failure-visual", "aws-visual", "scaling-visual",
    ):
        assert visual_id in parser.ids
    assert html.count('class="visual-caption"') >= 6
    assert 'role="img"' in html or '<figure' in html


def test_accessible_responsive_and_print_styles_exist():
    _, html = parse_course()
    for token in (
        ":focus-visible",
        "@media (max-width: 760px)",
        "@media (prefers-reduced-motion: reduce)",
        "@media print",
        ".sr-only",
        ".skip-link",
        "color-scheme: light",
    ):
        assert token in html
    assert "overflow-x: auto" in html
    assert "break-inside: avoid" in html


def test_course_has_navigation_and_progressive_disclosure():
    parser, html = parse_course()
    for element_id in (
        "course-header", "desktop-syllabus", "mobile-syllabus",
        "course-content", "course-finish", "course-live-region",
    ):
        assert element_id in parser.ids
    assert html.count('class="deep-dive"') >= 6
    assert html.count('class="model-answer"') >= 15
```

- [ ] **Step 2: Run the new tests and verify they fail on unfinished presentation**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: the four Task 1 tests PASS; one or more new presentation tests FAIL.

- [ ] **Step 3: Implement the finished visual system**

In embedded CSS, define exact design tokens and layout roles:

```css
:root {
  color-scheme: light;
  --navy-950: #0b1f33;
  --navy-800: #173b5e;
  --slate-700: #40566d;
  --slate-300: #c6d0da;
  --cream-100: #fbf7ed;
  --cream-200: #f1eadb;
  --paper: #fffdf8;
  --orange-600: #b84c1b;
  --orange-100: #fff0df;
  --green-700: #1f664c;
  --green-100: #e5f3ec;
  --ink: #17212b;
  --shadow: 0 18px 45px rgb(11 31 51 / 10%);
}
```

Implement:

- A full-width course header with restrained radial/linear background accents.
- `.course-shell` as a centered two-column grid with a 17rem sticky sidebar and
  a readable 48–54rem lesson column.
- Syllabus links with visible module number, title, time, and text completion
  badge; hide `#mobile-syllabus` on wide screens.
- Large editorial module headers, restrained card surfaces, callouts, term
  definitions, comparison grids, flow steps, code-shaped examples, and clear
  current-state versus future-state treatments.
- High-contrast buttons with hover, pressed, disabled, and `:focus-visible`
  states.
- Native disclosures styled so the summary remains recognizable as an action.
- At `max-width: 760px`, one-column layout, hidden desktop sidebar, visible
  mobile syllabus, full-width primary buttons, and compact diagrams.
- Under reduced motion, remove smooth scrolling, animation, and transitions.
- Print rules that remove navigation/progress/action controls, display all
  disclosure content, preserve URLs as text only when useful, and apply
  `break-inside: avoid` to modules, visuals, and callouts.

- [ ] **Step 4: Add the six explanatory visuals**

Implement each as a `<figure id="…">` with adjacent `.visual-caption` text:

1. `architecture-visual`: Browser/MCP → Caddy → FastAPI, then PostgreSQL/Redis,
   with Redis → Celery worker → source adapters and Redis pub/sub → SSE.
2. `refresh-visual`: eight numbered steps from stored `TrackedProduct` through
   discovery, normalized offers, fetch tasks, snapshot/history write, cache
   invalidation, publish, and alert evaluation.
3. `entity-visual`: `User → TrackedProduct → Offer → PricePoint` plus supporting
   `Alert`, `ClickEvent`, `DeadLetter`, `ApiToken`, and `ProductModel3D` records.
4. `failure-visual`: four labeled cards—404/410 delist, 429 skip cycle, transient
   retry with backoff, exhausted retry dead-letter—and their data consequences.
5. `aws-visual`: Internet/EIP/Caddy/web EC2 in the public subnet, RDS/Redis in
   private subnets, security-group-only data access, ECR/SSM/GitHub delivery.
6. `scaling-visual`: Current → 10x → 100x stages with explicit triggers and
   changes, ending with multi-instance web/workers, ALB/ECS, Multi-AZ data, and
   S3 model storage.

Prefer semantic boxes and connectors for responsive behavior. Use inline SVG
only for connectors that materially improve comprehension. Add prose that fully
explains every visual for screen-reader and print users.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 6: Commit the presentation layer**

```bash
git add tests/test_interview_course_artifact.py artifacts/deal-hunter-interview-course.html
git commit -m "Design the interview course learning experience"
```

---

### Task 3: Progress, Continuation, and Knowledge-Check Interactions

**Files:**
- Modify: `tests/test_interview_course_artifact.py`
- Modify: `artifacts/deal-hunter-interview-course.html`

**Interfaces:**
- Consumes: `[data-module-id]`, `.module-complete[data-module]`, `.knowledge-check[data-check-id]`, `#course-live-region`, syllabus anchors, and the three header controls.
- Produces: `loadState() -> CourseState`, `saveState(state)`, `renderProgress()`, `toggleModule(moduleId)`, `continueCourse()`, `resetProgress()`, `gradeCheck(checkElement)`, and `initializeCourse()`; persisted key `deal-hunter-interview-course:v1`.

- [ ] **Step 1: Add failing JavaScript interaction-contract tests**

Append:

```python
def test_progress_interaction_contract_is_embedded():
    _, html = parse_course()
    for function_name in (
        "loadState", "saveState", "renderProgress", "toggleModule",
        "continueCourse", "resetProgress", "gradeCheck", "initializeCourse",
    ):
        assert f"function {function_name}(" in html
    for token in (
        'const STORAGE_KEY = "deal-hunter-interview-course:v1"',
        "completedModules",
        "openDetails",
        "window.localStorage",
        "aria-pressed",
        "aria-live=\"polite\"",
        "window.confirm(",
    ):
        assert token in html


def test_state_is_versioned_and_answers_are_not_persisted():
    _, html = parse_course()
    assert "version: 1" in html
    save_start = html.index("function saveState(")
    save_end = html.index("function renderProgress(", save_start)
    save_body = html[save_start:save_end]
    assert "completedModules" in save_body
    assert "openDetails" in save_body
    assert "answer" not in save_body.lower()


def test_every_knowledge_check_has_feedback_and_explanation():
    _, html = parse_course()
    assert html.count('class="knowledge-check"') == 11
    assert html.count('class="check-feedback"') == 11
    assert html.count('class="check-explanation"') == 11
    assert html.count('class="check-answer"') >= 22
```

- [ ] **Step 2: Run the tests and verify interaction tests fail**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: the seven previous tests PASS; the three new tests FAIL.

- [ ] **Step 3: Add accessible knowledge checks**

Give each module one self-contained check:

- Use a `<fieldset>` and `<legend>` for single-choice or multiple-choice checks.
- Use radio inputs for one correct answer and checkboxes only when the prompt
  explicitly says “Choose all that apply.”
- Include at least two `.check-answer` labels.
- Add a `button.check-submit`, an initially empty `.check-feedback`, and a
  native `<details class="check-explanation">` that teaches why the correct
  choice is correct. Keep the explanation closed initially; it remains manually
  accessible without JavaScript and is opened automatically after grading.
- Do not make module completion depend on answering correctly.
- After grading, set feedback text to begin with `Correct:` or `Review:` so
  status never relies on color.

Cover these eleven checks in order: async request-path rationale, append-only
history, database uniqueness versus distributed lock, Redis blast radius,
failure classification, password versus API-token hashing, MCP ownership,
private subnet/security-group reasoning, first scaling measurement, Big O or
index selection, and honest limitation framing.

- [ ] **Step 4: Implement versioned local progress with graceful fallback**

Embed a final script and use this state contract:

```javascript
const STORAGE_KEY = "deal-hunter-interview-course:v1";
const STATE_VERSION = 1;
let storageAvailable = true;
let courseState = { version: 1, completedModules: [], openDetails: [] };

function loadState() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    if (!parsed || parsed.version !== STATE_VERSION ||
        !Array.isArray(parsed.completedModules) ||
        !Array.isArray(parsed.openDetails)) {
      return { version: 1, completedModules: [], openDetails: [] };
    }
    return {
      version: 1,
      completedModules: [...new Set(parsed.completedModules)],
      openDetails: [...new Set(parsed.openDetails)],
    };
  } catch (error) {
    storageAvailable = false;
    return { version: 1, completedModules: [], openDetails: [] };
  }
}

function saveState(state) {
  courseState = {
    version: 1,
    completedModules: state.completedModules,
    openDetails: state.openDetails,
  };
  if (!storageAvailable) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(courseState));
  } catch (error) {
    storageAvailable = false;
  }
}
```

`renderProgress()` must:

- Calculate completed count, percentage, and estimated remaining minutes from
  incomplete `[data-minutes]` values.
- Update `#overall-progress`, `#progress-label`, and `#remaining-time`.
- Update every completion button's text and `aria-pressed` value.
- Update matching desktop/mobile syllabus status text to `Complete` or `Not complete`.
- Announce user-triggered changes through `#course-live-region`.
- Show a non-blocking `Progress will not persist in this browser.` notice when
  `storageAvailable` is false.

- [ ] **Step 5: Implement controls, disclosure persistence, and grading**

Implement:

- `toggleModule(moduleId)`: add/remove the ID, save, render, and announce.
- `continueCourse()`: scroll/focus the first incomplete module heading; when all
  are complete, scroll/focus `#course-finish`.
- `resetProgress()`: call `window.confirm("Reset all course progress?")`; on
  confirmation restore the empty version-1 state, save, render, and announce.
- `gradeCheck(checkElement)`: validate that an answer is selected, compare
  selected values with the input(s) carrying `data-correct="true"`, reveal the
  explanation by setting its `open` property, prepend `Correct:` or `Review:`,
  and focus the feedback region.
- Detail `toggle` listeners: persist IDs only for `.deep-dive` disclosures with
  an ID. Do not persist `.model-answer` interview reveals.
- `initializeCourse()`: load state, restore deep-dive disclosures, bind all
  buttons/listeners, call `renderProgress()`, and run on `DOMContentLoaded`.
- `#print-course`: call `window.print()`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: 10 tests PASS.

- [ ] **Step 7: Commit interactions**

```bash
git add tests/test_interview_course_artifact.py artifacts/deal-hunter-interview-course.html
git commit -m "Add interview course progress and knowledge checks"
```

---

### Task 4: Browser QA, Accessibility Polish, and Full Verification

**Files:**
- Modify if findings require fixes: `artifacts/deal-hunter-interview-course.html`
- Modify if a missed contract is found: `tests/test_interview_course_artifact.py`

**Interfaces:**
- Consumes: Complete artifact and contract suite from Tasks 1–3.
- Produces: A visually verified, keyboard-usable, responsive, print-friendly final course.

- [ ] **Step 1: Run static validation and inspect document integrity**

Run:

```bash
pytest tests/test_interview_course_artifact.py -v
python - <<'PY'
from html.parser import HTMLParser
from pathlib import Path

class StrictEnoughParser(HTMLParser):
    def error(self, message):
        raise AssertionError(message)

path = Path("artifacts/deal-hunter-interview-course.html")
parser = StrictEnoughParser()
parser.feed(path.read_text(encoding="utf-8"))
print(f"parsed {path} successfully")
PY
```

Expected: 10 tests PASS and the parser prints a success line.

- [ ] **Step 2: Invoke the browser-use skill and open the artifact directly**

Use the browser automation skill to open:

```text
file:///Users/dhananjaysurti/personal/asset-generator/artifacts/deal-hunter-interview-course.html
```

Verify at a desktop viewport near 1440×1000:

1. Header, sidebar, and lesson column have a clear hierarchy.
2. Sidebar remains useful while scrolling without obscuring content.
3. All six diagrams are readable and their direction is unambiguous.
4. Body text has a comfortable line length and no dense wall-of-text sections.
5. Current-state, trade-off, and future-state callouts look distinct without
   relying only on color.

- [ ] **Step 3: Exercise interactions and persistence**

In the browser:

1. Complete modules 1 and 2; verify count, percentage, remaining time, both
   syllabus status labels, button text, and `aria-pressed` update.
2. Reload; verify those two completions remain.
3. Open a deep dive, reload, and verify it remains open.
4. Reveal a model interview answer, reload, and verify it is not persisted.
5. Use Continue; verify focus moves to module 3.
6. Submit an unanswered knowledge check; verify helpful validation.
7. Submit an incorrect answer and then a correct answer; verify explanatory
   `Review:` and `Correct:` feedback.
8. Cancel reset once, then confirm reset; verify the state clears.
9. Complete all modules; verify Continue targets the completion footer and the
   completion state remains restrained under reduced motion.

- [ ] **Step 4: Verify responsive, keyboard, reduced-motion, and print behavior**

At a mobile viewport near 390×844:

1. Desktop sidebar is hidden and mobile syllabus is usable.
2. No page-level horizontal scrolling occurs.
3. Diagrams, tables, and buttons fit or scroll within their own bounded region.
4. Primary actions are comfortably tappable.

Using keyboard-only navigation:

1. Activate the skip link.
2. Traverse syllabus links, disclosures, knowledge checks, completion buttons,
   and header controls in a logical order.
3. Confirm every interactive control has a visible focus indicator.

Emulate reduced motion and verify no smooth scroll, animation, or transition is
required. Open print preview and verify navigation/actions disappear, lesson
content and model answers are visible, and figures do not clip.

- [ ] **Step 5: Fix each browser finding and add a regression assertion when practical**

For each issue, make the smallest HTML/CSS/JS correction. When the issue has a
stable static signature—missing label, absent media rule, wrong hook, external
resource, or state-field mismatch—add a focused assertion to
`tests/test_interview_course_artifact.py`. Rerun:

```bash
pytest tests/test_interview_course_artifact.py -v
```

Expected: all focused tests PASS after every correction.

- [ ] **Step 6: Run the complete project test suite with Redis available**

Start the existing Redis service and override the host-side test URL:

```bash
docker compose up -d redis
REDIS_URL=redis://localhost:6379/0 pytest -q
```

Expected: all 180 existing tests plus the new course tests PASS. If the exact
count changes because repository tests were added concurrently, require zero
failures and report the observed count rather than preserving 180 as a claim.

- [ ] **Step 7: Review the final diff for accidental scope or factual drift**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~3..HEAD
```

Confirm only the course artifact and its focused test changed after the approved
spec/plan commits, no external dependencies were introduced, and the course does
not present future improvements as current behavior.

- [ ] **Step 8: Commit QA fixes if the browser pass changed files**

If files changed during QA:

```bash
git add tests/test_interview_course_artifact.py artifacts/deal-hunter-interview-course.html
git commit -m "Polish interview course accessibility and responsive behavior"
```

If browser QA required no changes, do not create an empty commit.

---

## Final handoff

Report:

- The absolute clickable path to the standalone course.
- The modules, progress, knowledge checks, print support, and responsive behavior delivered.
- Focused and full-suite test results with exact observed counts.
- Browser viewport and interaction checks performed.
- Any intentionally deferred limitations from the approved design.
