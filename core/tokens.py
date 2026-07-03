"""Personal access tokens for the remote MCP endpoint (Phase 8).

Same threat model as GitHub PATs: the plaintext exists only in the creation
response; we persist a SHA-256 digest and compare digests on every request.
SHA-256 (not bcrypt) is fine here because the token itself is high-entropy
random — there's nothing to brute-force offline the way there is with a
human-chosen password.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import ApiToken, User

TOKEN_PREFIX = "dh_live_"


def generate_token() -> tuple[str, str]:
    """Return (plaintext, sha256_hex). Plaintext is shown once, never stored."""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def resolve_token(db: Session, plain: str | None) -> User | None:
    """Look up the user owning a presented token, or None.

    The prefix check is a cheap reject for obviously-wrong values (and stray
    JWTs) before we bother hashing. Stamps `last_used_at` so the UI can show
    whether a token is live before the user revokes it.
    """
    if not plain or not plain.startswith(TOKEN_PREFIX):
        return None
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(plain)))
    if row is None:
        return None
    # naive UTC to match the server_default(func.now()) columns
    row.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return db.get(User, row.user_id)
