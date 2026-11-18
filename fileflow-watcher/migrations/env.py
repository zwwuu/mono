"""Alembic environment for the FileFlow ledger database.

The target metadata is the SQLAlchemy ``Base`` from the project's
ledger module, so ``alembic revision --autogenerate`` picks up model
changes. The database URL comes from the environment (see alembic.ini);
the ledger file is the one named in the agent/watcher config, passed
here as FILEFLOW_LEDGER_DB (or the full URL as FILEFLOW_DB_URL).
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the src/ layout importable when alembic runs from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_watcher.infrastructure.persistence import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.environ.get("FILEFLOW_DB_URL")
if not db_url:
    ledger_db = os.environ.get("FILEFLOW_LEDGER_DB", "fileflow.db")
    # Like the ledger itself, create the parent dir on demand so a
    # fresh deploy path does not fail with "unable to open database file".
    Path(ledger_db).expanduser().parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{ledger_db}"
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


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
