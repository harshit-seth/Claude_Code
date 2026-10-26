"""SQLite database setup and session lifecycle."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from agent.server.models import Base

DEFAULT_DATABASE_PATH = Path.home() / ".shree" / "shree.db"


def create_database(
    database_url: str | None = None,
) -> tuple[Engine, sessionmaker[Session]]:
    """Create the SQLite engine, schema, and session factory."""
    if database_url is None:
        DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    options = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=options)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def get_session_factory(
    factory: sessionmaker[Session],
) -> Iterator[Session]:
    """Yield one short-lived database session for a request."""
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _enable_sqlite_foreign_keys(connection, _record) -> None:
    """Enable SQLite's foreign key enforcement for each connection."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
