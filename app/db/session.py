from collections.abc import Generator

from sqlalchemy import DDL, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.configs.settings import get_settings

# Keep database-generated timestamps in the same timezone as app-created ones.
POSTGRES_TIMEZONE = "UTC"
POSTGRES_CONNECT_ARGS = {"options": f"-c timezone={POSTGRES_TIMEZONE}"}

# Tables whose rows must never change after insert. Triggers below reject any
# UPDATE or DELETE at the DB layer so the audit trail cannot drift via ad-hoc
# queries, debugger sessions, or future code paths.
IMMUTABLE_TABLES = ("quotes", "quote_legs", "executions", "ledger_entries", "rate_snapshots")


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().resolved_database_url,
    future=True,
    pool_pre_ping=True,
    connect_args=POSTGRES_CONNECT_ARGS,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


_IMMUTABLE_FN = DDL(
    """
CREATE OR REPLACE FUNCTION fx_raise_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table %% is append-only and immutable', TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
"""
)

_IMMUTABLE_TRIGGERS = DDL(
    "\n".join(
        f"""
DROP TRIGGER IF EXISTS {table}_immutable ON {table};
CREATE TRIGGER {table}_immutable
    BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION fx_raise_immutable();
"""
        for table in IMMUTABLE_TABLES
    )
)

event.listen(Base.metadata, "after_create", _IMMUTABLE_FN.execute_if(dialect="postgresql"))
event.listen(Base.metadata, "after_create", _IMMUTABLE_TRIGGERS.execute_if(dialect="postgresql"))


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def create_all() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
