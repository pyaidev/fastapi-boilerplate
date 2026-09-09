from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )
    app_name: str = "fastapi-boilerplate"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    secret_key: SecretStr
    outbox_encryption_key: SecretStr
    database_url: SecretStr
    redis_url: SecretStr
    celery_broker_url: SecretStr = SecretStr("redis://redis:6379/0")
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    cors_origins: list[str] = []
    trusted_proxy_ips: list[str] = []
    public_base_url: str = "http://localhost:8002"
    docs_enabled: bool = True
    jwt_issuer: str = "fastapi-boilerplate"
    jwt_audience: str = "fastapi-api"
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    rate_ip: str = "1000/minute"
    rate_user: str = "600/minute"
    rate_login: str = "5/minute"
    rate_register: str = "5/hour"
    rate_reset: str = "5/hour"
    rate_refresh: str = "30/minute"
    email_host: str = "mailpit"
    email_port: int = 1025
    email_use_tls: bool = False
    email_user: str = ""
    email_password: SecretStr = SecretStr("")
    email_from: str = "noreply@example.com"
    sentry_dsn: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""

    @field_validator("rate_ip", "rate_user", "rate_login", "rate_register", "rate_reset", "rate_refresh")
    @classmethod
    def valid_rate(cls, value: str) -> str:
        count, separator, period = value.partition("/")
        if (
            not separator
            or not count.isdigit()
            or int(count) < 1
            or period not in {"second", "minute", "hour", "day"}
        ):
            raise ValueError("Rate must be a positive count/second, count/minute, count/hour or count/day")
        return value

    @model_validator(mode="after")
    def safe_configuration(self) -> "Settings":
        key = self.secret_key.get_secret_value()
        if len(key) < 48 or len(set(key)) < 10 or key.startswith("CHANGE_ME"):
            raise ValueError("SECRET_KEY must contain at least 48 random characters")
        try:
            Fernet(self.outbox_encryption_key.get_secret_value().encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("OUTBOX_ENCRYPTION_KEY must be a Fernet key") from exc
        if not self.database_url.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        if not self.redis_url.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must be a Redis URL")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("Use explicit ALLOWED_HOSTS")
        if "*" in self.cors_origins:
            raise ValueError("Use explicit CORS_ORIGINS")
        if self.app_env == "production":
            if self.debug or self.docs_enabled:
                raise ValueError("Disable DEBUG and DOCS_ENABLED in production")
            if urlparse(self.public_base_url).scheme != "https":
                raise ValueError("Production PUBLIC_BASE_URL must use HTTPS")
            if not self.email_use_tls:
                raise ValueError("Production SMTP must use TLS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
