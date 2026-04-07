"""Configuration management using Pydantic Settings."""

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LiteLLM Configuration
    LLM_API_URL: str = "http://localhost:4000/v1"
    LLM_API_KEY: str = "sk-1234"

    # Storage
    LOCAL_DATA_DIR: str = "./data"
    DB_NAME: str = "membrain.db"

    # Manifests
    MANIFESTS_DIR: str = "./manifests"

    # Server
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 9574
    BACKEND_RELOAD: bool = False

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # Conversation
    RESPONSE_CONTEXT_WINDOW: int = 25
    ROUTER_CONTEXT_WINDOW: int = 25
    CONTEXT_TRUNK_SIZE: int = 5  # rounds to drop at once when window overflows

    # Reflection
    REFLECTION_START_TURNS: int = 10  # minimum user turns in session before first reflection
    REFLECTION_INTERVAL_TURNS: int = 10  # turns since last cursor before next reflection
    REFLECTION_LOOKBACK_MSGS: int = 6  # extra messages before cursor to include as context

    # MemBrain backend integration
    MEMBRAIN_BASE_URL: str | None = None  # None = all MemBrain calls become no-ops
    MEMBRAIN_TIMEOUT: float = 5.0  # search timeout in seconds

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @cached_property
    def DATABASE_URL(self) -> str:
        """SQLite connection URL."""
        db_path = Path(self.LOCAL_DATA_DIR).resolve() / self.DB_NAME
        return f"sqlite+aiosqlite:///{db_path}"


settings = Settings()
