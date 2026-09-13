import os
import secrets
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=False
    )

    APP_TOKEN: str = Field(
        default_factory=lambda: os.environ.get("APP_TOKEN")
        or secrets.token_urlsafe(32)
    )
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"
    PORT: int = int(os.environ.get("PORT", "8000")) if os.environ.get("PORT") else 8000

    AGENTROUTER_BASE_URL: Optional[str] = None
    AGENTROUTER_API_KEY: Optional[str] = None
    AGENTROUTER_DEFAULT_MODEL: str = "gemini-3.8-flash"

    # Secondary OpenAI-compatible provider (routed by model name, e.g. deepseek-*).
    BAI_BASE_URL: Optional[str] = "https://api.b.ai/v1"
    BAI_API_KEY: Optional[str] = None

    E2B_API_KEY: Optional[str] = None

    GITHUB_PAT: Optional[str] = None

    # Stable key material for encrypting connector secrets at rest.
    # Falls back to APP_TOKEN so existing deployments keep working.
    CONNECTOR_SECRET: Optional[str] = None

    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_SUBJECT: str = "mailto:admin@example.com"

    DEFAULT_MAX_ITERATIONS: int = 50
    DEFAULT_TOKEN_BUDGET: int = 2000000
    DEFAULT_COMMAND_TIMEOUT_S: int = 600
    DEFAULT_APPROVAL_TIMEOUT_S: int = 1800

    # How many tasks the background worker runs at once.
    WORKER_CONCURRENCY: int = 3

    # Sandbox lifetime (seconds); extended on every agent iteration via keep_alive().
    SANDBOX_TIMEOUT_S: int = 3600


settings = Settings()
