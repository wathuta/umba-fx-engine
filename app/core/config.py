from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults used when local environment values are not set.
DEFAULT_RATE_FRESHNESS_SECONDS = 300
DEFAULT_RATE_PROVIDER_TIMEOUT_SECONDS = 2.0

# Buy/sell spreads intentionally share the same default unless configured otherwise.
DEFAULT_SPREAD_BPS = 50


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://fx_user:fx_password@localhost:55432/fx_takehome"
    rate_freshness_seconds: int = DEFAULT_RATE_FRESHNESS_SECONDS
    rate_provider_url: str = "https://fxapi.app/api/usd.json"
    rate_provider_timeout_seconds: float = DEFAULT_RATE_PROVIDER_TIMEOUT_SECONDS
    default_buy_spread_bps: int = DEFAULT_SPREAD_BPS
    default_sell_spread_bps: int = DEFAULT_SPREAD_BPS

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
