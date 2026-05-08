from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# SQLAlchemy engine safety settings are explicit because they affect DB connectivity behavior.
SQLALCHEMY_FUTURE_MODE_ENABLED = True
POOL_PRE_PING_ENABLED = True

# Session transaction defaults keep commits explicit in services.
AUTOFLUSH_ENABLED = False
AUTOCOMMIT_ENABLED = False


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().database_url,
    future=SQLALCHEMY_FUTURE_MODE_ENABLED,
    pool_pre_ping=POOL_PRE_PING_ENABLED,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=AUTOFLUSH_ENABLED,
    autocommit=AUTOCOMMIT_ENABLED,
    future=SQLALCHEMY_FUTURE_MODE_ENABLED,
)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def create_all() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
