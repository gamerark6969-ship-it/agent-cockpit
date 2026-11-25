"""Durable phone controls for coding tasks running on a separate cloud worker."""

import sqlalchemy as sa
from alembic import op

revision = "0006_task_controls"
down_revision = "0005_video_privacy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "task_controls" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("task_controls",
            sa.Column("id",sa.String(36),primary_key=True),
            sa.Column("task_id",sa.String(36),sa.ForeignKey("tasks.id",ondelete="CASCADE"),nullable=False),
            sa.Column("kind",sa.String(16),nullable=False),
            sa.Column("message",sa.Text(),nullable=False),
            sa.Column("processed_at",sa.DateTime(timezone=True),nullable=True),
            sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
        op.create_index("ix_task_controls_task_id","task_controls",["task_id"])


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
