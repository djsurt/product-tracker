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


def test_course_has_navigation_and_progressive_disclosure():
    parser, html = parse_course()
    for element_id in (
        "course-header", "desktop-syllabus", "mobile-syllabus",
        "course-content", "course-finish", "course-live-region",
    ):
        assert element_id in parser.ids
    assert html.count('class="deep-dive"') >= 6
    assert html.count('class="model-answer"') >= 15
