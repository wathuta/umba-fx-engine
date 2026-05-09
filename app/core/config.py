from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults used when local environment values are not set.
DEFAULT_RATE_FRESHNESS_SECONDS = 300
DEFAULT_RATE_PROVIDER_TIMEOUT_SECONDS = 2.0

# Buy/sell spreads intentionally share the same default unless configured otherwise.
DEFAULT_SPREAD_BPS = 50


class Settings(BaseSettings):
    postgres_db: str = "fx_takehome"
    postgres_user: str = "fx_user"
    postgres_password: str = "fx_password"
    postgres_host: str = "localhost"
    postgres_port: int = 55432
    # Optional override for CI, production, or managed database providers.
    database_url: str | None = None
    rate_freshness_seconds: int = DEFAULT_RATE_FRESHNESS_SECONDS
    rate_provider_url: str = "https://fxapi.app/api/usd.json"
    rate_provider_timeout_seconds: float = DEFAULT_RATE_PROVIDER_TIMEOUT_SECONDS
    default_buy_spread_bps: int = DEFAULT_SPREAD_BPS
    default_sell_spread_bps: int = DEFAULT_SPREAD_BPS

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
