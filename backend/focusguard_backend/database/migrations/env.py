"""Alembic environment (spec section 32).

Two deliberate choices:

* The URL comes from the environment, never from alembic.ini. A connection string
  in a committed file is a credential in version control.
* `render_as_batch=True` for SQLite, which cannot ALTER most columns. Without it,
  the first migration that changes a column works on PostgreSQL and fails on the
  development database - discovered at the worst possible moment.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from focusguard_backend.database.session import database_url
from focusguard_backend.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", database_url())
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
