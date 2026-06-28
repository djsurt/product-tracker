"""Password hashing and JWT helpers.

Why these choices:
- **bcrypt** for passwords: a deliberately slow, salted hash. We never store
  plaintext; even a DB leak doesn't immediately reveal passwords. We call the
  `bcrypt` library directly (rather than passlib) to avoid a well-known
  version-compat warning between passlib and modern bcrypt.
- **JWT (HS256)**: a signed, stateless token. The server can verify a request
  is authenticated without a session lookup — it just checks the signature.
  Tradeoff: tokens can't be revoked before they expire, so we keep expiry short.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from core.settings import get_settings

settings = get_settings()


def hash_password(plain: str) -> str:
    """Hash a plaintext password for storage."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a login attempt against the stored hash (constant-time compare)."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(subject: str) -> str:
    """Mint a signed JWT whose `sub` claim is the user id."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Verify signature + expiry and return the claims. Raises on invalid token."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
