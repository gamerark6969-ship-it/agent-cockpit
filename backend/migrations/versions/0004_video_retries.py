"""Persist bounded retry and processing backoff across scheduled workers."""

import sqlalchemy as sa
from alembic import op

revision = "0004_video_retries"
down_revision = "0003_video_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("automation_runs")}
    if "retry_count" not in columns:
        op.add_column("automation_runs", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    if "next_attempt_at" not in columns:
        op.add_column("automation_runs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
