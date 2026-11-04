from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings

# Configure engine depending on whether SQLite or MySQL is used
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine_kwargs = {}
if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Recommended settings for MySQL connections
    engine_kwargs.update({
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    })

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency that creates a new database session per request and closes it after."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initializes tables in database and creates default demo users if missing."""
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)

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
                    __import__("sqlalchemy").text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type} DEFAULT {default_val}")
                )
                conn.commit()
            except Exception:
                # Column already exists or not supported
                pass

    # Seed default mock users for easy first run
    from app.services.auth_service import seed_initial_users
    db = SessionLocal()
    try:
        seed_initial_users(db)
    finally:
        db.close()

