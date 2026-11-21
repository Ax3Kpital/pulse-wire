"""Alembic environment. The DB URL comes from the caller config or app settings."""

from alembic import context

from app.config.settings import get_settings
from app.db import make_engine
from app.models.tables import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _url()
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(url)
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # Batch migrations rebuild a table (copy, DROP, rename). With foreign keys ON, the
            # DROP would cascade-delete every child row (e.g. story_members), so turn them off
            # for the migration (must happen outside a transaction) and verify afterwards.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            connection.commit()
            problems = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"foreign key violations after migration: {problems[:5]}")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
