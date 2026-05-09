from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Keep database-generated timestamps in the same timezone as app-created ones.
POSTGRES_TIMEZONE = "UTC"
POSTGRES_CONNECT_ARGS = {"options": f"-c timezone={POSTGRES_TIMEZONE}"}


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().resolved_database_url,
    future=True,
    pool_pre_ping=True,
    connect_args=POSTGRES_CONNECT_ARGS,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def create_all() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
