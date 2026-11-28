"""Per-series YouTube playlist and bounded licensed chart track slots."""

import sqlalchemy as sa
from alembic import op

revision = "0010_chart_music"
down_revision = "0009_reminders"
branch_labels = None
depends_on = None


def upgrade():
    db = sa.inspect(op.get_bind())
    if "chart_playlist" not in {col["name"] for col in db.get_columns("automation_series")}:
        op.add_column("automation_series", sa.Column("chart_playlist", sa.String(128)))
    if not db.has_table("automation_chart_tracks"):
        op.create_table("automation_chart_tracks",
            sa.Column("series_id", sa.String(36), sa.ForeignKey("automation_series.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("slot", sa.Integer(), primary_key=True),
            sa.Column("video_id", sa.String(11), nullable=False),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("audio", sa.LargeBinary(), nullable=False),
            sa.UniqueConstraint("series_id", "video_id"), sa.CheckConstraint("slot BETWEEN 1 AND 3"))


def downgrade():
    op.drop_table("automation_chart_tracks")
    with op.batch_alter_table("automation_series") as batch:
        batch.drop_column("chart_playlist")
