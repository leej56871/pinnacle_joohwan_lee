from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application Server
    PORT: int = Field(default=8000, description="Uvicorn listening port")

    # PostgreSQL Database Connection
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://tx_user:tx_password@postgres:5432/tx_db",
        description="Async SQLAlchemy database connection URI",
    )

    # Redis Connection
    REDIS_URL: str = Field(
        default="redis://redis:6379/0",
        description="Redis server connection URI",
    )

    # Distributed Concurrency & Idempotency Timeouts (Seconds)
    LOCK_TIMEOUT_SECONDS: int = Field(
        default=30,
        description="TTL for the in-progress atomic execution lock",
    )
    IDEMPOTENCY_TTL_SECONDS: int = Field(
        default=86400,
        description="TTL for caching completed transaction responses (24 hours)",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()