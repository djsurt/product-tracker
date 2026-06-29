"""Alembic environment.

Pulls the DB URL from our app settings (single source of truth) and the target
metadata from core.db.Base so `alembic revision --autogenerate` sees our models.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.db import Base, _normalize_db_url
from core.settings import get_settings

# Import models so they register on Base.metadata (no-op in Phase 0).
import core.models  # noqa: F401

config = context.config
# Normalize so a managed host's bare postgres:// URL gets the psycopg driver too.
config.set_main_option("sqlalchemy.url", _normalize_db_url(get_settings().database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
