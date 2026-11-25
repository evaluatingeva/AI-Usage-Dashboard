from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. In production use the SQL in /backend/migrations (or Alembic)."""
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            # Turn usage_events into a TimescaleDB hypertable when the extension exists.
            has_ts = conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'")
            ).scalar()
            if has_ts:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                conn.execute(
                    text(
                        "SELECT create_hypertable('usage_events','ts', if_not_exists => TRUE,"
                        " migrate_data => TRUE)"
                    )
                )
