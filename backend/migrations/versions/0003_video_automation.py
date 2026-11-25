"""Add durable video-series, run, OAuth, and credential tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_video_automation"
down_revision = "0002_worker_leases"
branch_labels = None
depends_on = None


def _create_table_if_missing(name: str, *columns, **kwargs) -> None:
    if name not in inspect(op.get_bind()).get_table_names():
        op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _create_table_if_missing(
        "automation_series",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("publish_times", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("publish_mode", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("voice", sa.String(length=80), nullable=False),
        sa.Column("made_for_kids", sa.Boolean(), nullable=False),
        sa.Column("contains_synthetic_media", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_table_if_missing(
        "automation_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("series_id", sa.String(length=36), sa.ForeignKey("automation_series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("render_data", sa.JSON(), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_session_enc", sa.Text(), nullable=True),
        sa.Column("upload_session_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("youtube_video_id", sa.String(length=32), nullable=True),
        sa.Column("youtube_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("series_id", "scheduled_for", name="uq_automation_run_slot"),
    )
    _create_index_if_missing("ix_automation_runs_series_id", "automation_runs", ["series_id"])
    _create_index_if_missing("ix_automation_runs_status", "automation_runs", ["status"])
    _create_index_if_missing("ix_automation_runs_status_created", "automation_runs", ["status", "created_at"])
    _create_index_if_missing("ix_automation_runs_lease", "automation_runs", ["lease_expires_at"])
    _create_index_if_missing("ix_automation_runs_lease_owner", "automation_runs", ["lease_owner"])
    _create_table_if_missing(
        "automation_run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("automation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_automation_run_events_run_id", "automation_run_events", ["run_id"])
    _create_index_if_missing("ix_automation_run_events_run_created", "automation_run_events", ["run_id", "created_at"])
    _create_table_if_missing(
        "automation_youtube_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tokens_enc", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=True),
        sa.Column("channel_title", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_table_if_missing(
        "automation_oauth_states",
        sa.Column("state_hash", sa.String(length=64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
