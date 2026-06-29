"""Thin SMTP email sender (Phase 4).

Deliberately tiny: build a MIME message and hand it to an SMTP server. In
development that server is MailHog, which captures everything and never
delivers — so we can watch alert emails land in a web UI without sending real
mail. In cloud you'd point SMTP_* at a provider (SendGrid/Resend/SES).

The notification *logic* (which alerts fire, debounce) lives in
workers/notifications.py and stays unaware of how delivery happens — it just
calls a `send` function. That keeps the rules engine unit-testable with a fake
sender and no SMTP server.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from core.settings import get_settings

settings = get_settings()


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email via the configured SMTP server.

    Raises on failure so the calling Celery task can retry — a notification we
    silently drop is worse than one we retry.
    """
    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)
