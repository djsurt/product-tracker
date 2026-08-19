from __future__ import annotations

import re
import subprocess
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


def run_inline_script(harness: str) -> dict:
    """Evaluate the real course script with a small, dependency-free DOM seam."""
    _, html = parse_course()
    script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    runner = f"""
const fs = require("fs");
const vm = require("vm");
const context = {{
  console,
  Set,
  window: {{ matchMedia: () => ({{ matches: false }}) }},
  document: {{ addEventListener: () => {{}}, documentElement: {{ classList: {{ add: () => {{}} }} }} }},
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(0, "utf8"), context);
const result = vm.runInContext({harness!r}, context);
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", runner],
        input=script,
        text=True,
        check=True,
        capture_output=True,
    )
    import json

    return json.loads(completed.stdout)


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


def test_course_describes_current_repository_behavior_without_overclaiming():
    parser, _ = parse_course()
    text = " ".join(parser.text).lower()
    for claim in (
        "early acknowledgement",
        "worker dies mid-task",
        "explicit retries",
        "overlapping schedules",
        "any valid three-letter currency",
        "cross-currency",
        "background discovery logs",
        "only interactive mcp search",
        "terminal success or failure",
        "branch-scoped",
        "wildcard resources",
        "claude vision",
    ):
        assert claim in text
    assert "queue delivery is at-least-once" not in text
    assert "assume a narrowly scoped aws role" not in text
    assert "with live progress" not in text


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


def test_required_visuals_and_text_alternatives_exist():
    parser, html = parse_course()
    for visual_id in (
        "architecture-visual", "refresh-visual", "entity-visual",
        "failure-visual", "aws-visual", "scaling-visual",
    ):
        assert visual_id in parser.ids
    assert html.count('class="visual-caption"') >= 6
    assert 'role="img"' in html or "<figure" in html


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


def test_programmatic_navigation_respects_reduced_motion():
    _, html = parse_course()
    focus_start = html.index("function focusAndScroll(")
    focus_end = html.index("function continueCourse(", focus_start)
    focus_body = html[focus_start:focus_end]
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)").matches' in focus_body
    assert 'behavior: reduceMotion ? "auto" : "smooth"' in focus_body


def test_print_hides_quiz_actions_and_reveals_disclosures():
    _, html = parse_course()
    print_styles = html.split("@media print", 1)[1].split("</style>", 1)[0]
    hidden_selectors = [
        selectors
        for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]+)\}", print_styles)
        if "display:none!important" in declarations.replace(" ", "")
    ]
    assert any(
        ".check-submit" in selectors
        and '.knowledge-check input[type="radio"]' in selectors
        for selectors in hidden_selectors
    )
    assert "details:not([open]) > :not(summary) { display: block; }" in print_styles


def test_print_reflows_diagrams_without_horizontal_scrolling():
    _, html = parse_course()
    print_styles = html.split("@media print", 1)[1].split("</style>", 1)[0]
    assert ".visual { overflow: visible; max-width: 100%; }" in print_styles
    assert (
        ".visual-grid, .flow-grid, .stage-grid, .architecture-route, "
        ".architecture-data, .failure-grid, .entity-chain, .supporting-records "
        "{ min-width: 0; max-width: 100%; }"
    ) in print_styles


def test_course_has_navigation_and_progressive_disclosure():
    parser, html = parse_course()
    for element_id in (
        "course-header", "desktop-syllabus", "mobile-syllabus",
        "course-content", "course-finish", "course-live-region",
    ):
        assert element_id in parser.ids
    assert html.count('class="deep-dive"') >= 6
    assert html.count('class="model-answer"') >= 15


def test_architecture_visual_draws_complete_request_and_data_flows():
    _, html = parse_course()
    architecture = html.split('<figure id="architecture-visual"', 1)[1].split(
        "</figure>", 1
    )[0]
    assert '<div class="flow-label">Caddy → FastAPI</div>' in architecture
    assert '<div class="flow-label">FastAPI → PostgreSQL + Redis</div>' in architecture


def test_mobile_syllabus_repeats_each_module_time_and_completion_badge():
    _, html = parse_course()
    mobile_syllabus = html.split('<details id="mobile-syllabus"', 1)[1].split(
        "</details>", 1
    )[0]
    assert mobile_syllabus.count('class="syllabus-meta"') == 11
    assert mobile_syllabus.count('class="syllabus-badge"') == 11
    for number, title, minutes in (
        ("01", "Project story", "12 min"),
        ("02", "Architecture", "16 min"),
        ("03", "Data model", "14 min"),
        ("04", "Async + Redis", "17 min"),
        ("05", "Reliability", "15 min"),
        ("06", "API + security", "15 min"),
        ("07", "AI + MCP", "12 min"),
        ("08", "AWS + CI/CD", "22 min"),
        ("09", "Scaling", "15 min"),
        ("10", "Fundamentals", "25 min"),
        ("11", "Practice", "20 min"),
    ):
        assert f"{number} · {title}" in mobile_syllabus
        assert minutes in mobile_syllabus


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
        'aria-live="polite"',
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


def test_malformed_and_structurally_invalid_state_is_discarded_without_disabling_storage():
    result = run_inline_script(
        """
const outcomes = [];
for (const stored of [
  "{broken",
  JSON.stringify({version: 2, completedModules: [], openDetails: []}),
  JSON.stringify({version: 1, completedModules: "bad", openDetails: []}),
  JSON.stringify({version: 1, completedModules: [42], openDetails: []}),
]) {
  const operations = [];
  window.localStorage = {
    getItem: () => stored,
    removeItem: (key) => operations.push(["remove", key]),
    setItem: (key, value) => operations.push(["set", key, JSON.parse(value)]),
  };
  storageAvailable = true;
  const loaded = loadState();
  saveState({version: 1, completedModules: ["project-story"], openDetails: []});
  outcomes.push({loaded, operations, storageAvailable});
}
outcomes;
"""
    )
    for outcome in result:
        assert outcome["loaded"] == {
            "version": 1,
            "completedModules": [],
            "openDetails": [],
        }
        assert outcome["storageAvailable"] is True
        assert [operation[0] for operation in outcome["operations"]] == ["remove", "set"]
        assert outcome["operations"][1][2]["completedModules"] == ["project-story"]


def test_storage_access_failure_disables_persistence():
    result = run_inline_script(
        """
window.localStorage = {getItem: () => { throw new Error("blocked"); }};
storageAvailable = true;
const loaded = loadState();
({loaded, storageAvailable});
"""
    )
    assert result == {
        "loaded": {"version": 1, "completedModules": [], "openDetails": []},
        "storageAvailable": False,
    }


def test_all_complete_state_reveals_textual_completion_message():
    result = run_inline_script(
        """
const classNames = new Set();
const footer = {classList: {toggle: (name, enabled) => enabled ? classNames.add(name) : classNames.delete(name)}};
const completionMessage = {hidden: true};
const values = {};
const modules = Array.from({length: 11}, (_, index) => ({
  id: `module-${index}`,
  dataset: {moduleId: `module-${index}`, minutes: "1"},
}));
document.querySelector = (selector) => ({
  "#overall-progress": values.overall ||= {},
  "#progress-label": values.label ||= {},
  "#remaining-time": values.remaining ||= {},
  "#course-progress": values.progress ||= {},
  "#storage-notice": values.notice ||= {},
  "#course-finish": footer,
  "#course-completion-message": completionMessage,
}[selector]);
document.querySelectorAll = (selector) => selector === "[data-module-id]" ? modules : [];
courseState = {version: 1, completedModules: modules.map((module) => module.dataset.moduleId), openDetails: []};
renderProgress();
({completeClass: classNames.has("course-complete"), messageHidden: completionMessage.hidden, progress: values.label.textContent});
"""
    )
    assert result == {
        "completeClass": True,
        "messageHidden": False,
        "progress": "100% complete",
    }


def test_static_fallback_hides_javascript_only_buttons_and_keeps_course_content():
    _, html = parse_course()
    button_tags = re.findall(r"<button\b[^>]*>", html)
    assert button_tags
    assert (
        "html:not(.js-enabled) #course-header button, "
        "html:not(.js-enabled) .module-complete, "
        "html:not(.js-enabled) .check-submit { display: none; }"
    ) in html
    noscript = html.split("<noscript>", 1)[1].split("</noscript>", 1)[0].lower()
    for phrase in (
        "progress tracking and interactive controls are unavailable",
        "lessons, questions, explanations, and model answers remain readable",
    ):
        assert phrase in noscript


def test_finish_checklist_covers_failure_story_scaling_and_reduced_motion():
    _, html = parse_course()
    footer = html.split('<footer id="course-finish"', 1)[1].split("</footer>", 1)[0]
    assert "failure story" in footer.lower()
    assert "scaling answer" in footer.lower()
    assert 'id="course-completion-message"' in footer
    assert "@keyframes" not in html
    reduced_motion = html.split("@media (prefers-reduced-motion: reduce)", 1)[1].split(
        "@media print", 1
    )[0]
    assert "transition-duration: .01ms !important" in reduced_motion


def test_every_knowledge_check_has_feedback_and_explanation():
    _, html = parse_course()
    assert html.count('class="knowledge-check"') == 11
    assert html.count('class="check-feedback"') == 11
    assert html.count('class="check-explanation"') == 11
    assert html.count('class="check-answer"') >= 22
