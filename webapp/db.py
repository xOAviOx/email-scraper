"""Database engine + session factory.

Defaults to a local SQLite file so the app runs with zero setup. In
production set DATABASE_URL to your Postgres URL (e.g. the Supabase
connection string) and nothing else changes.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///webapp.db")

# check_same_thread is a SQLite-only quirk; the worker and web process both
# touch the same file so we must allow cross-thread use.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False,
                            expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    from . import models  # noqa: F401  (registers the mapped classes)
    Base.metadata.create_all(engine)
