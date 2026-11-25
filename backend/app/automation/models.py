"""Database records for durable YouTube video automation."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..models import Base, new_id, utcnow


class VideoSeries(Base):
    __tablename__ = "automation_series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    publish_times: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["09:00"])
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    publish_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="review")
    privacy: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    voice: Mapped[str] = mapped_column(String(80), nullable=False, default="en-US-AriaNeural")
    made_for_kids: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contains_synthetic_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class VideoRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        UniqueConstraint("series_id", "scheduled_for", name="uq_automation_run_slot"),
        Index("ix_automation_runs_status_created", "status", "created_at"),
        Index("ix_automation_runs_lease", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    series_id: Mapped[str] = mapped_column(String(36), ForeignKey("automation_series.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="generate")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    render_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upload_session_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class VideoRunEvent(Base):
    __tablename__ = "automation_run_events"
    __table_args__ = (Index("ix_automation_run_events_run_created", "run_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("automation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class YouTubeCredential(Base):
    __tablename__ = "automation_youtube_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tokens_enc: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class OAuthState(Base):
    __tablename__ = "automation_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
