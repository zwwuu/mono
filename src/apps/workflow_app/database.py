"""workflow-app's database: shared setup from common.db, app-specific init."""

from sqlalchemy import text

from apps.workflow_app.config import settings
from common.db import Base, Database

__all__ = ["Base", "SessionLocal", "db", "engine", "get_db", "init_db"]

db = Database(settings.DATABASE_URL)
engine = db.engine
SessionLocal = db.SessionLocal
get_db = db.get_db


def init_db():
    """Initializes tables in database and creates default demo users if missing."""
    import apps.workflow_app.models  # noqa: F401
    db.create_all()

    # Lightweight migration check for existing user table columns
    with engine.connect() as conn:
        try:
            # Check for roles column in users table
            conn.execute(Base.metadata.tables["users"].select().limit(1))
        except Exception:
            pass
        # SQLite / MySQL safe column additions
        for col_name, col_type, default_val in [
            ("roles", "TEXT", "'[]'"),
            ("workflow_roles", "TEXT", "'{}'")
        ]:
            try:
                conn.execute(
                    text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type} DEFAULT {default_val}")
                )
                conn.commit()
            except Exception:
                # Column already exists or not supported
                pass

    # Seed default mock users for easy first run
    from apps.workflow_app.services.auth_service import seed_initial_users
    session = SessionLocal()
    try:
        seed_initial_users(session)
    finally:
        session.close()
