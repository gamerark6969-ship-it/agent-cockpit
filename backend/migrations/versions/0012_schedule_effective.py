"""Track when the current schedule became active."""
from alembic import op
import sqlalchemy as sa

revision = "0012_schedule_effective"
down_revision = "0011_youtube_client"
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns("automation_series")
    if not any(col["name"] == "schedule_effective_at" for col in columns):
        op.add_column("automation_series", sa.Column("schedule_effective_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("automation_series", "schedule_effective_at")
