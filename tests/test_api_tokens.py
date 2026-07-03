"""Tests for API tokens (Phase 8): model + generate/hash/resolve helpers."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import ApiToken, User

import core.models  # noqa: F401  (register tables on Base.metadata)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(email="t@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def test_api_token_round_trip(db, user):
    row = ApiToken(user_id=user.id, name="my-laptop", token_hash="a" * 64)
    db.add(row)
    db.commit()

    got = db.scalar(select(ApiToken).where(ApiToken.token_hash == "a" * 64))
    assert got is not None
    assert got.user_id == user.id
    assert got.name == "my-laptop"
    assert isinstance(got.created_at, datetime)
    assert got.last_used_at is None
