# Deal Hunter Interview Course — Design

## Goal

Create a polished, self-contained course that helps the project owner refresh
Deal Hunter and prepare to discuss it in a new-grad software engineering
interview. The artifact must work both as a guided two-to-three-hour course and
as a fast reference immediately before an interview.

The course should teach repository-specific facts, connect those facts to
general computer-science and system-design fundamentals, and provide active
recall practice. It complements the existing AWS interview quiz rather than
replacing or duplicating it.

## Deliverable and constraints

- Create `artifacts/deal-hunter-interview-course.html` as one standalone file.
- Use semantic HTML, embedded CSS, and vanilla JavaScript.
- Require no server, build step, package installation, or network connection.
- Do not load external fonts, scripts, images, stylesheets, analytics, or APIs.
- Store optional course progress only in browser `localStorage` under a
  course-specific key. Do not transmit or collect user data.
- Make the page useful with JavaScript unavailable: all instructional content
  must remain readable, while progress tracking and knowledge-check feedback
  may require JavaScript.
- Support current desktop and mobile browsers, keyboard navigation, reduced
  motion, and printing.

## Audience and learning outcomes

The primary audience is the project's author preparing for new-grad interviews.
After completing the course, the learner should be able to:

1. Give clear 30-second and 60-second descriptions of Deal Hunter.
2. Trace product creation, price refresh, notification, SSE, MCP, and deployment
   flows end to end.
3. Explain the purpose and trade-offs of FastAPI, PostgreSQL, Redis, Celery,
   HTMX/Jinja, SSE, Terraform, and the selected AWS services.
4. Defend key reliability decisions, including idempotency, retries, 429
   backpressure, delisting, dead letters, and cache invalidation.
5. Relate the implementation to new-grad fundamentals such as data structures,
   complexity, indexing, transactions, HTTP, processes, threads, and races.
6. State the system's limitations honestly and propose a credible scaling path.
7. Answer common technical and behavioral interview questions using concise,
   evidence-based responses.

## Information architecture

The course contains eleven ordered modules:

1. **Project story** — user problem, product value, concise pitches, personal
   contribution framing, and the phased delivery narrative.
2. **System architecture** — component responsibilities plus end-to-end
   add-product and refresh-price flows.
3. **Database and data modeling** — entities, relationships, UUIDs, money,
   constraints, indexes, append-only history, and denormalized snapshots.
4. **Asynchronous processing and Redis** — Celery, Beat, at-least-once delivery,
   idempotency locks, cache-aside, rate limiting, metrics, pub/sub, and quotas.
5. **Reliability and failure handling** — distinct treatment of 404/410, 429,
   transient server/network errors, exhausted retries, partial source failures,
   and notification consistency.
6. **API, security, and live UI** — FastAPI responsibilities, authentication
   versus authorization, JWTs, hashed MCP tokens, HTMX/Jinja, and SSE trade-offs.
7. **AI and MCP features** — Claude screenshot identification, six MCP tools,
   Meshy 3D/AR generation, configuration gates, quotas, and graceful degradation.
8. **AWS and CI/CD** — request path, VPC and subnet layout, security groups,
   EC2/RDS/ElastiCache/ECR/SSM/Caddy, GitHub OIDC, Terraform state, migrations,
   deployment, smoke testing, cost, and availability trade-offs.
9. **Scaling and system design** — present bottlenecks, measurement, staged
   evolution at 10x and 100x, and justified service boundaries.
10. **New-grad fundamentals** — data structures, Big O, SQL, indexing,
    transactions, HTTP, DNS/TLS, processes, threads, locks, and I/O versus CPU;
    every concept uses a Deal Hunter example.
11. **Interview practice** — technical prompts, model answers, STAR prompts,
    common mistakes, and a final readiness checklist.

Each module follows a consistent rhythm:

1. A brief statement of what the learner will be able to explain.
2. Two to five concise lesson sections.
3. At least one visual, comparison, code-shaped example, or flow where it adds
   explanatory value.
4. A short knowledge check or active-recall prompt.
5. A module summary and explicit completion control.

## Page architecture

The document has four major regions:

### Course header

The header introduces the course, shows overall progress, estimates remaining
time, and offers `Continue learning`, `Print study guide`, and `Reset progress`
actions. The reset action requires confirmation.

### Syllabus navigation

On wide screens, a sticky left sidebar lists every module, its estimated time,
and completion state. On narrow screens, the syllabus becomes a compact
disclosure above the lesson. Navigation uses normal anchor links so it remains
functional without JavaScript.

### Lesson column

The main column contains all modules in document order. Longer supporting
material uses native `details` and `summary` elements so the core narrative is
scannable without hiding content from non-JavaScript users. Interview prompts
use revealable model answers. Comparison tables become stacked labeled blocks
on narrow screens rather than overflowing horizontally.

### Completion footer

The footer summarizes completed modules, presents the final readiness checklist,
and recommends the next study action. It may link to the repository's existing
AWS quiz only if that quiz is present on the implementation branch at the final
artifact's known relative path; otherwise the link is omitted. The course is
complete without the quiz.

## Visual and interaction design

The visual system uses deep navy, warm cream, restrained orange, slate, and a
high-contrast green completion color. Headings use a system sans-serif stack;
long-form text uses a readable system serif stack. No font download is allowed.

The presentation should feel editorial and calm rather than like an operations
dashboard. Visual hierarchy comes from typography, spacing, borders, and
background tone. Shadows are subtle and limited to high-level surfaces.

Required visuals include:

- A component architecture flow from browser/MCP client through Caddy and
  FastAPI to PostgreSQL, Redis, Celery, and marketplace sources.
- A numbered refresh pipeline showing discovery, normalized offers, fetches,
  price history, cache invalidation, pub/sub, SSE, and notifications.
- A compact entity-relationship diagram for the principal ORM models.
- A failure-semantics comparison for 404/410, 429, transient failures, and
  exhausted retries.
- An AWS topology separating public and private subnets.
- A staged scaling path from the current single-host deployment to a more
  resilient multi-instance architecture.

Visuals should use semantic HTML and inline SVG where needed. They must include
text alternatives or adjacent prose and cannot rely on color alone.

JavaScript progressively adds:

- Module completion toggles and overall progress.
- `Continue learning`, which navigates to the first incomplete module.
- Immediate knowledge-check feedback with explanations.
- Persistent disclosure state only where it benefits continuation.
- Reset progress with confirmation.
- A small celebratory completion state that respects reduced-motion settings.

The page does not include accounts, cloud synchronization, timers, search,
gamified points, or a full exam engine. Those features do not materially improve
the refresher's primary purpose.

## Content requirements

Repository-specific claims must be grounded in the current code and docs. The
course must distinguish current behavior from suggested future improvements.
It must not invent usage metrics, performance numbers, users, revenue, or team
contributions.

The content must explicitly cover:

- Thin FastAPI request handling and background marketplace work.
- The `PriceSource`/`NormalizedOffer` adapter boundary.
- The `User -> TrackedProduct -> Offer -> PricePoint` data path and associated
  alert, click, dead-letter, token, and 3D-model records.
- Numeric money values and the current lack of FX normalization.
- Celery's at-least-once behavior and layered idempotency controls.
- Redis's multiple roles and resulting blast-radius trade-off.
- Cache-aside reads and invalidation after price writes.
- SSE versus polling and WebSockets.
- Personal access token hashing and per-user MCP authorization.
- The current AWS cost/reliability trade-offs, including the single EC2 host,
  single-AZ data services, no ALB, no NAT Gateway, and no automated rollback.
- Immutable image-tag deployment as an improvement over pulling `latest`.
- A credible, measurement-led scaling plan rather than an automatic jump to
  microservices.
- New-grad fundamentals illustrated by concrete project examples.

## Accessibility and responsive behavior

- Use landmarks, ordered heading levels, meaningful link/button labels, native
  form controls, and visible `:focus-visible` states.
- Announce progress and knowledge-check changes through a polite live region.
- Never encode status through color alone; include icons or text labels.
- Respect `prefers-reduced-motion` and avoid required animation.
- At widths up to 760px, use one column, full-width primary actions, a compact
  syllabus disclosure, and non-overflowing diagrams/tables.
- Provide a print stylesheet that removes interactive chrome, expands hidden
  lesson/model-answer content, preserves diagram legibility, and avoids splitting
  major cards when practical.
- Meet WCAG 2.1 AA contrast targets for normal and large text.

## State and privacy

The course stores a versioned object in `localStorage`, for example:

```json
{
  "version": 1,
  "completedModules": ["project-story", "architecture"],
  "openDetails": ["architecture-request-flow"]
}
```

Malformed, missing, or older state is ignored safely. Progress is advisory, not
security-sensitive. Knowledge-check answers are not retained because completion
and continuation are the only persistent needs.

## Error handling

- If storage is unavailable, the course remains usable for the current session
  and shows a quiet non-blocking notice that progress will not persist.
- If stored state is malformed, discard it and initialize clean state.
- If JavaScript fails or is disabled, all lessons, diagrams, prompts, and model
  answers remain accessible through the static document and native disclosures.
- No runtime network errors are possible because the artifact makes no requests.

## Verification strategy

Add `tests/test_interview_course_artifact.py` using Python's standard-library
HTML parser and text assertions. Tests will verify:

1. The artifact exists and contains all eleven uniquely identified modules.
2. Core project terms and limitations are represented.
3. Required diagram, progress, knowledge-check, completion, reset, and live-region
   hooks exist.
4. The document contains viewport, responsive, reduced-motion, focus, and print
   styles.
5. No external resource URLs, `fetch`, `XMLHttpRequest`, remote script/style
   references, analytics, or form submission endpoints exist.
6. The progress state has an explicit version and course-specific storage key.

Browser verification will exercise:

1. Initial rendering at desktop and mobile widths.
2. Sidebar/mobile syllabus navigation.
3. Knowledge checks and feedback.
4. Module completion, progress updates, continuation, reload persistence, and
   reset confirmation.
5. Keyboard focus order and disclosure controls.
6. Print preview and reduced-motion behavior.

Visual inspection will confirm hierarchy, spacing, diagram readability, text
contrast, overflow, and that the page feels like one coherent course rather
than a collection of unrelated cards.

## Success criteria

The artifact is complete when a learner can open it directly, study the entire
project in a coherent sequence, resume browser-local progress, practice the
most likely interview questions, print a useful reference, and understand both
the system's strengths and its limitations without consulting the source code.
