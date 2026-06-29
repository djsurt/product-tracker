"""Database engine, session factory, and the declarative Base.

Models (added in Phase 1) will subclass `Base`. Alembic imports `Base.metadata`
to autogenerate migrations, so this is the single source of truth for schema.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.settings import get_settings

settings = get_settings()


def _normalize_db_url(url: str) -> str:
    """Force the psycopg (v3) driver onto bare Postgres URLs.

    Managed hosts (e.g. Render) hand out `postgres://` / `postgresql://` URLs,
    but SQLAlchemy needs an explicit driver. Rewriting here means the platform's
    connection string works unchanged in DATABASE_URL.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


engine = create_engine(_normalize_db_url(settings.database_url), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base all ORM models inherit from."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
