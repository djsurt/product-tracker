"""Shared FastAPI dependencies.

`get_current_user` is the gate on every protected endpoint. It extracts the
bearer token, verifies it, and loads the user — failing with 401 if anything is
off. Routers just declare `user: User = Depends(get_current_user)` and get a
guaranteed-authenticated user, keeping auth logic in one place (DRY).
"""

import uuid

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from core.db import get_db
from core.models import User
from core.security import decode_token

# Name of the httponly cookie the HTML frontend (Phase 6) stores its JWT in.
SESSION_COOKIE = "access_token"

# tokenUrl powers the "Authorize" button in Swagger UI (/docs).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

_credentials_exc = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise _credentials_exc
    except jwt.PyJWTError:
        raise _credentials_exc

    user = db.get(User, uuid.UUID(user_id))
    if user is None:
        raise _credentials_exc
    return user


# --- Cookie-based auth for the HTML frontend (Phase 6) ---------------------
# The JSON API uses a bearer header (above). A server-rendered HTMX app is more
# naturally driven by an httponly session cookie the browser sends automatically
# — so the same signed JWT is just carried in a cookie instead of a header.
def _user_from_cookie(request: Request, db: Session) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        user_id = decode_token(token).get("sub")
    except jwt.PyJWTError:
        return None
    if not user_id:
        return None
    return db.get(User, uuid.UUID(user_id))


def get_optional_web_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """Current user from the session cookie, or None (for public pages)."""
    return _user_from_cookie(request, db)


def require_web_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Gate for protected pages: redirect to /login when not signed in.

    A 303 with a Location header sends a browser to the login page; the extra
    `HX-Redirect` header makes htmx do a full-page redirect too, so an expired
    session mid-interaction lands the user back at login instead of swapping in
    an error fragment.
    """
    user = _user_from_cookie(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login", "HX-Redirect": "/login"},
        )
    return user
