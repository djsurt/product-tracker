"""Shared FastAPI dependencies.

`get_current_user` is the gate on every protected endpoint. It extracts the
bearer token, verifies it, and loads the user — failing with 401 if anything is
off. Routers just declare `user: User = Depends(get_current_user)` and get a
guaranteed-authenticated user, keeping auth logic in one place (DRY).
"""

import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from core.db import get_db
from core.models import User
from core.security import decode_token

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
