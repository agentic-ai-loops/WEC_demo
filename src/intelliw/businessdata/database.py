"""SQLite engine and sessions for #businessdata (one database per workspace)."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from intelliw.businessdata.tables import Base


def create_db_engine(path: Path | None) -> Engine:
    """Engine for the database at `path`, or an in-memory database if `path` is None.

    Foreign-key enforcement is switched on for every connection (SQLite leaves it off).
    """
    url = f"sqlite:///{path}" if path is not None else "sqlite://"
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables that do not exist yet."""
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
