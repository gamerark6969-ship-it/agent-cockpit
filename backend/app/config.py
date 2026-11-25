import os
import secrets
from typing import Optional

from pydantic import Field, field_validator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=False
    )

    ENVIRONMENT: str = "development"

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

    # Optional routes. No paid provider is enabled by supplying a model name alone.
    XINGCHI_BASE_URL: Optional[str] = None
    XINGCHI_API_KEY: Optional[str] = None
    XINGCHI_API_STYLE: str = "responses"
    AGENTROUTER_OPENAI_BASE_URL: str = "https://agentrouter.org/v1"
    AGENTROUTER_OPENAI_API_KEY: Optional[str] = None
    FREEGG_BASE_URL: str = "https://freegg.app/v1"
    FREEGG_API_KEY: Optional[str] = None
    GENERATION_TIMEOUT_S: int = Field(default=180, gt=0, le=600)
    OPENCODE_BASE_URL: str = "https://opencode.ai/inference/openai/v1"
    OPENCODE_API_KEY: Optional[str] = None
    OPENCODE_BIN: str = "opencode"
    OPENCODE_REMOTE_WORKER: bool = False
    OPENCODE_TIMEOUT_S: int = Field(default=240, gt=0, le=900)
    LOCAL_LLM_ENABLED: bool = False
    LOCAL_LLM_REMOTE_WORKER: bool = False
    LOCAL_LLM_BASE_URL: str = "http://127.0.0.1:11434"
    LOCAL_LLM_MODEL: str = "qwen3:1.7b"

    E2B_API_KEY: Optional[str] = None
    SANDBOX_BACKEND: str = "e2b"
    DOCKER_SANDBOX_IMAGE: str = "cockpit-sandbox:local"
    DOCKER_SANDBOX_DIR: str = "data/sandboxes"

    GITHUB_PAT: Optional[str] = None

    # Stable key material for encrypting connector secrets at rest. Production
    # requires this to be persistent and separate from APP_TOKEN.
    CONNECTOR_SECRET: Optional[str] = None

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    AUTOMATION_FRONTEND_URL: Optional[str] = None

    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_SUBJECT: str = "mailto:admin@example.com"
    DEFAULT_MAX_ITERATIONS: int = 120
    DEFAULT_TOKEN_BUDGET: int = 2000000
    DEFAULT_COMMAND_TIMEOUT_S: int = 600
    DEFAULT_APPROVAL_TIMEOUT_S: int = 1800

    # Public origin of this backend (Render sets RENDER_EXTERNAL_URL). Used to
    # build absolute fallback artifact URLs for deploy_site.
    PUBLIC_BASE_URL: Optional[str] = None

    # Public preview links are signed, expire automatically, and are throttled
    # per client address to limit artifact enumeration and scraping.
    PREVIEW_URL_TTL_S: int = Field(default=86400, gt=0)
    PREVIEW_RATE_LIMIT: int = Field(default=120, gt=0)
    PREVIEW_RATE_WINDOW_S: int = Field(default=60, gt=0)

    # Global API rate limit (per client address, sliding window). Exempts
    # /api/health so uptime checks and keepalive pings are never throttled.
    API_RATE_LIMIT: int = Field(default=240, gt=0)
    API_RATE_WINDOW_S: int = Field(default=60, gt=0)
    API_RATE_LIMIT_ENABLED: bool = True

    # How many tasks the background worker runs at once.
    WORKER_CONCURRENCY: int = 3
    BACKGROUND_WORKER_ENABLED: bool = True

    # Maximum number of non-terminal tasks allowed across the application.
    MAX_ACTIVE_TASKS: int = Field(default=1, gt=0)

    # Maximum wall-clock runtime for the complete task lifecycle.
    MAX_TASK_RUNTIME_S: int = Field(default=1800, gt=0)

    # Maximum persisted event history retained for each task.
    MAX_EVENTS_PER_TASK: int = Field(default=1000, gt=0)

    # Sandbox lifetime (seconds); extended on every agent iteration via keep_alive().
    SANDBOX_TIMEOUT_S: int = Field(default=3600, gt=0)

    # Stop sandboxes attached to inactive tasks after this many idle seconds.
    SANDBOX_IDLE_TIMEOUT_S: int = Field(default=900, gt=0)

    # How often the worker scans for idle sandboxes.
    SANDBOX_CLEANUP_INTERVAL_S: int = Field(default=60, gt=0)

    @field_validator("DATABASE_URL")
    @classmethod
    def async_database_url(cls, value: str) -> str:
        if value.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1).replace("postgresql://", "postgresql+asyncpg://", 1)
            parts = urlsplit(value)
            query = dict(parse_qsl(parts.query))
            if "sslmode" in query:
                query["ssl"] = query.pop("sslmode")
            query.pop("channel_binding", None)  # libpq option; asyncpg uses its own TLS handling.
            return urlunsplit(parts._replace(query=urlencode(query)))
        return value

    @property
    def cors_origins(self) -> list:
        origins = [origin.strip().rstrip("/") for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        if self.AUTOMATION_FRONTEND_URL:
            frontend = urlsplit(self.AUTOMATION_FRONTEND_URL.strip())
            if frontend.scheme in ("http", "https") and frontend.netloc:
                frontend_origin = f"{frontend.scheme}://{frontend.netloc}"
                if frontend_origin not in origins:
                    origins.append(frontend_origin)
        return origins

    def validate_production_secrets(self) -> None:
        if self.ENVIRONMENT.lower() not in ("production", "prod"):
            return
        if not os.environ.get("APP_TOKEN"):
            raise RuntimeError("APP_TOKEN must be explicitly configured in production")
        if not self.CONNECTOR_SECRET:
            raise RuntimeError("CONNECTOR_SECRET must be explicitly configured in production")
        if secrets.compare_digest(self.APP_TOKEN, self.CONNECTOR_SECRET):
            raise RuntimeError("APP_TOKEN and CONNECTOR_SECRET must be different in production")
        if not self.cors_origins or "*" in self.cors_origins:
            raise RuntimeError("CORS_ORIGINS must contain explicit origins in production")


settings = Settings()
