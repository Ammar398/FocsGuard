"""Database engine and session management (spec section 32).

SQLITE IN DEVELOPMENT, POSTGRESQL IN PRODUCTION
-----------------------------------------------
Both are supported from one URL. Nothing in the models uses a dialect-specific
type, so moving to PostgreSQL is a configuration change rather than a migration
of the code.

SQLite needs two things PostgreSQL does not:

* ``check_same_thread=False``, because FastAPI serves requests on a thread pool;
* ``PRAGMA foreign_keys=ON``, because SQLite **silently ignores** foreign keys
  otherwise. A cascade that works in tests and quietly leaves orphans in
  production is exactly the kind of bug worth spending three lines to prevent.

WAL is enabled for the same reason as the agent's local store: readers must not
block the writer.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from focusguard_shared.utilities import get_logger

logger = get_logger("backend.database")

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def database_url() -> str:
    """Resolve the database URL from the environment, defaulting to SQLite."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return normalise_url(url)

    path = Path(os.environ.get("FOCUSGUARD_BACKEND_DB", "focusguard_backend.db"))
    return f"sqlite:///{path.resolve()}"


def normalise_url(url: str) -> str:
    """Point a hosted Postgres URL at the psycopg 3 driver.

    Hosts such as Render hand out `postgres://...` or `postgresql://...`.
    SQLAlchemy reads both as "use psycopg2", which is not installed - psycopg 3
    is. Any other URL (SQLite, or one that already names a driver) is unchanged.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _configure_sqlite(dbapi_connection, _record) -> None:
    """Enable the pragmas SQLite needs to behave like a real database."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine, _SessionFactory

    if _engine is not None and url is None:
        return _engine

    resolved = url or database_url()
    is_sqlite = resolved.startswith("sqlite")

    engine = create_engine(
        resolved,
        echo=echo,
        future=True,
        connect_args={"check_same_thread": False} if is_sqlite else {},
        pool_pre_ping=not is_sqlite,
    )

    if is_sqlite:
        event.listen(engine, "connect", _configure_sqlite)

    if url is None:
        _engine = engine
        _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
        logger.info("Database engine ready (%s)", resolved.split("://", 1)[0])

    return engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that always closes."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def create_all(engine: Engine | None = None) -> None:
    """Create tables directly.

    Alembic owns the schema in production; this exists for tests and for a first
    local run, where a migration round-trip would be pure friction.
    """
    from focusguard_backend.models.base import Base

    target = engine or get_engine()
    Base.metadata.create_all(target)
    add_missing_columns(target)


def add_missing_columns(engine: Engine) -> list[str]:
    """Add new *nullable* columns to tables that already exist.

    `create_all` creates missing tables but never changes an existing one, so a
    server that keeps its database between deploys (the hosted one) would
    otherwise lack every column added since it was first created. Only nullable
    columns are added: they need no value for the rows already there. Anything
    more involved belongs in an Alembic migration.
    """
    from sqlalchemy import inspect, text

    from focusguard_backend.models.base import Base

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present or not column.nullable:
                    continue
                kind = column.type.compile(dialect=engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}')
                )
                added.append(f"{table.name}.{column.name}")
    if added:
        logger.info("Added columns to the existing database: %s", ", ".join(added))
    return added


def reset_engine() -> None:
    """Drop the cached engine. Used by tests to switch databases."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
