"""Settings isolated from the main application settings namespace."""

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutomationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOMATION_", env_file=None, extra="ignore", case_sensitive=False
    )

    enabled: bool = True
    poll_interval_s: float = Field(default=3.0, ge=0.25, le=60)
    lease_seconds: int = Field(default=120, ge=15, le=3600)
    storage_dir: Path = Path("data/automation")
    storage_persistent: bool = False
    public_base_url: str | None = None
    media_url_ttl_s: int = Field(default=3600, ge=60, le=604800)
    youtube_client_file: Path | None = None
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_redirect_uri: str | None = None
    youtube_callback_path: str = "/api/automation/youtube/callback"
    frontend_url: str | None = None
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    media_retention_days: int = Field(default=7, ge=1, le=30)
    media_storage_limit_mb: int = Field(default=750, ge=50, le=1000)
    github_repository: str | None = None
    github_token: str | None = None
    github_workflow: str = "video-production.yml"
    github_ref: str = "main"
    retry_limit: int = Field(default=2, ge=0, le=5)
    retry_delay_s: int = Field(default=300, ge=30, le=3600)
    missed_slot_grace_minutes: int = Field(default=120, ge=0, le=1440)
    schedule_horizon_hours: int = Field(default=30, ge=1, le=72)

    def youtube_credentials(self) -> tuple[str, str] | None:
        if not self.youtube_redirect_uri:
            return None
        if self.youtube_client_id or self.youtube_client_secret:
            if self.youtube_client_id and self.youtube_client_secret:
                return self.youtube_client_id, self.youtube_client_secret
            return None
        if self.youtube_client_file is None:
            return None
        try:
            with self.youtube_client_file.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                return None
            web = json.loads(raw)["web"]
            client_id, secret = web["client_id"], web["client_secret"]
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None
        if all(isinstance(value, str) and value.strip() for value in (client_id, secret)):
            return client_id, secret
        return None

    @property
    def youtube_configured(self) -> bool:
        return self.youtube_credentials() is not None

    @property
    def s3_configured(self) -> bool:
        return bool(self.s3_endpoint and self.s3_access_key and self.s3_secret_key and self.s3_bucket)


automation_settings = AutomationSettings()
