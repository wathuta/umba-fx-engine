from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://fx_user:fx_password@localhost:55432/fx_takehome"
    rate_freshness_seconds: int = 300
    rate_provider_url: str = "https://api.exchangeratesapi.io/v1/latest"
    rate_provider_api_key: str | None = None
    rate_provider_timeout_seconds: float = 2.0
    default_buy_spread_bps: int = 50
    default_sell_spread_bps: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
