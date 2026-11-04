"""Tests for common.db: shared SQLAlchemy setup for apps."""

from unittest import mock

import pytest
from sqlalchemy import Integer, Text, inspect, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from common.db import Base, Database, create_engine_for


class Widget(Base):
    __tablename__ = "widgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


def sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path}/test.db"


def test_create_engine_for_sqlite_disables_thread_check():
    with mock.patch("common.db.create_engine") as create:
        create_engine_for("sqlite:///x.db")
    _, kwargs = create.call_args
    assert kwargs["connect_args"] == {"check_same_thread": False}


def test_create_engine_for_other_dbs_enables_pooling():
    with mock.patch("common.db.create_engine") as create:
        create_engine_for("mysql+pymysql://user@host/db")
    _, kwargs = create.call_args
    assert kwargs["pool_size"] == 10
    assert kwargs["max_overflow"] == 20
    assert kwargs["pool_recycle"] == 3600
    assert kwargs["pool_pre_ping"] is True


def test_create_engine_for_overrides_win():
    with mock.patch("common.db.create_engine") as create:
        create_engine_for("sqlite:///x.db", connect_args={"check_same_thread": True})
    _, kwargs = create.call_args
    assert kwargs["connect_args"] == {"check_same_thread": True}


def test_database_get_db_yields_and_closes_session(tmp_path):
    db = Database(sqlite_url(tmp_path))
    try:
        with mock.patch.object(Session, "close", autospec=True) as close_mock:
            gen = db.get_db()
            session = next(gen)
            assert isinstance(session, Session)
            with pytest.raises(StopIteration):
                next(gen)
            close_mock.assert_called_once_with(session)
    finally:
        db.engine.dispose()


def test_database_create_all_creates_model_tables(tmp_path):
    db = Database(sqlite_url(tmp_path))
    db.create_all()
    try:
        with db.engine.connect() as conn:
            assert inspect(conn).has_table("widgets")
            conn.execute(text("INSERT INTO widgets (name) VALUES ('w1')"))
            conn.commit()
            count = conn.execute(text("SELECT COUNT(*) FROM widgets")).scalar()
            assert count == 1
    finally:
        db.engine.dispose()


def test_database_session_roundtrip(tmp_path):
    db = Database(sqlite_url(tmp_path))
    db.create_all()
    try:
        session = db.SessionLocal()
        try:
            session.add(Widget(name="w2"))
            session.commit()
        finally:
            session.close()
        session = db.SessionLocal()
        try:
            assert session.query(Widget).count() == 1
        finally:
            session.close()
    finally:
        db.engine.dispose()
