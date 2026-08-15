from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

import app.models  # noqa: F401
from app.config.settings import get_settings
from app.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url = config.get_main_option("sqlalchemy.url")
    connect_args = {"timeout": 30} if database_url.startswith("sqlite") else {}
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    if database_url.startswith("sqlite"):

        @event.listens_for(connectable, "connect")
        def _set_sqlite_busy_timeout(
            dbapi_connection, connection_record
        ):  # noqa: ANN001, ARG001
            dbapi_connection.execute("PRAGMA busy_timeout=30000")

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
