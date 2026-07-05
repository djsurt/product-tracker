"""Tests for the 3D/AR preview feature (Phase 10)."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import ProductModel3D, TrackedProduct, User


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _product(db):
    user = User(email=f"{uuid.uuid4()}@x.com", hashed_password="h")
    db.add(user)
    db.flush()
    tp = TrackedProduct(user_id=user.id, title="XM5", query="xm5")
    db.add(tp)
    db.flush()
    return tp


def test_product_model3d_defaults_and_unique(db):
    tp = _product(db)
    row = ProductModel3D(tracked_product_id=tp.id, source_image_url="http://i/x.jpg")
    db.add(row)
    db.commit()
    assert row.status == "pending"
    assert row.glb_path is None
    db.add(ProductModel3D(tracked_product_id=tp.id, source_image_url="http://i/y.jpg"))
    with pytest.raises(Exception):
        db.commit()
    db.rollback()
