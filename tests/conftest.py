from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.configs.settings import get_settings
from app.utils.money import Currency
from app.db.models import CurrentRate, RateRefresh, RateSnapshot
from app.db.session import Base, SessionLocal, engine
from app.main import app
from app.services.rates import CANONICAL_PAIRS


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def seed_rates(session, fetched_at: datetime | None = None) -> None:
    fetched_at = fetched_at or datetime.now(UTC)
    refresh = RateRefresh(
        provider="test",
        status="completed",
        provider_base_currency="USD",
        fetched_at=fetched_at,
        pairs_updated=0,
        duration_ms=1,
    )
    session.add(refresh)
    session.flush()
    rates = {
        Currency.USD: Decimal("1"),
        Currency.EUR: Decimal("0.8000000000"),
        Currency.KES: Decimal("130.0000000000"),
        Currency.NGN: Decimal("1500.0000000000"),
    }
    settings = get_settings()
    updated = 0
    for base, quote in CANONICAL_PAIRS:
        mid_rate = rates[quote] / rates[base]
        snapshot = RateSnapshot(
            rate_refresh_id=refresh.id,
            base_currency=base.value,
            quote_currency=quote.value,
            mid_rate=mid_rate,
            provider="test",
            fetched_at=fetched_at,
            raw_payload_hash="test",
        )
        session.add(snapshot)
        session.flush()
        current = CurrentRate(
            base_currency=base.value,
            quote_currency=quote.value,
            mid_rate=mid_rate,
            buy_spread_bps=settings.default_buy_spread_bps,
            sell_spread_bps=settings.default_sell_spread_bps,
            rate_snapshot_id=snapshot.id,
            last_updated_at=fetched_at,
        )
        session.add(current)
        updated += 1
    refresh.pairs_updated = updated
    session.commit()


@pytest.fixture
def seeded_rates(db_session):
    seed_rates(db_session)
