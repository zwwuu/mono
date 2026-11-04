"""Shared SQLAlchemy setup for apps: engine, sessions, declarative Base.

Needs SQLAlchemy (an app dependency, not stdlib — unlike the rest of
``common``).

Usage:
    from common.db import Base, Database
    from sqlalchemy import Integer, Text
    from sqlalchemy.orm import Mapped, mapped_column

    class User(Base):
        __tablename__ = "users"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    db = Database("sqlite:///./app.db")

    # FastAPI dependency: one session per request, always closed
    @app.get("/users")
    def list_users(session: Session = Depends(db.get_db)):
        ...

    # create tables once all models are imported
    db.create_all()
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

__all__ = ["Base", "Database", "create_engine_for"]

Base = declarative_base()


def create_engine_for(url, **overrides):
    """Create a SQLAlchemy engine with sensible defaults.

    SQLite gets ``check_same_thread=False`` (needed for threaded
    servers); anything else gets connection pooling
    (``pool_size``/``max_overflow``/``pool_recycle``/``pool_pre_ping``).
    Explicit overrides always win.
    """
    kwargs = dict(overrides)
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    else:
        kwargs.setdefault("pool_size", 10)
        kwargs.setdefault("max_overflow", 20)
        kwargs.setdefault("pool_recycle", 3600)
        kwargs.setdefault("pool_pre_ping", True)
    return create_engine(url, **kwargs)


class Database:
    """One app's database: engine, session factory, table creation."""

    def __init__(self, url, **engine_kwargs):
        self.engine = create_engine_for(url, **engine_kwargs)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_db(self):
        """Yield a session per call; closed afterwards.

        Use as a FastAPI dependency: ``Depends(db.get_db)``.
        """
        session: Session = self.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def create_all(self):
        """Create all tables for the models imported so far."""
        Base.metadata.create_all(bind=self.engine)
