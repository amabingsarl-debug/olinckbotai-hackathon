from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OLINCK BOT AI"
    environment: str = "development"
    secret_key: str = Field(default="change-me-before-production")
    access_token_expire_minutes: int = 720
    database_url: str = "postgresql+asyncpg://olinck:olinck@postgres:5432/olinck"
    redis_url: str = "redis://redis:6379/0"
    trading_mode: str = "paper"
    real_trading_enabled: bool = False
    scheduler_secret: str | None = None
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    gateio_api_key: str | None = None
    gateio_api_secret: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    alert_email_to: str | None = None
    news_rss_feeds: list[str] = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]
    datahub_enabled: bool = False
    datahub_demo_mode: bool = True
    datahub_gms_url: str | None = None
    datahub_token: str | None = None
    datahub_platform: str = "olinckbotai"
    datahub_mcp_server_url: str | None = None
    cockroach_enabled: bool = False
    cockroach_demo_mode: bool = True
    cockroach_database_url: str | None = None
    cockroach_vector_dimensions: int = 16
    cockroach_mcp_server_url: str | None = None
    aws_region: str = "us-east-1"
    aws_s3_reports_bucket: str | None = None
    aws_ecs_cluster: str | None = None
    aws_ecs_service: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("trading_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"paper", "real", "backtest"}:
            raise ValueError("TRADING_MODE must be paper, real, or backtest")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
