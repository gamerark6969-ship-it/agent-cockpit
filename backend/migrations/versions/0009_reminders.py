"""Personal reminders."""
from alembic import op
import sqlalchemy as sa

revision = "0009_reminders"
down_revision = "0008_series_music"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("reminders"):
        return
    op.create_table("reminders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("due", sa.BigInteger(), nullable=False),
        sa.Column("next_attempt", sa.BigInteger()),
        sa.Column("done", sa.Boolean(), nullable=False))
    op.create_index("ix_reminders_due", "reminders", ["due"])
    op.create_index("ix_reminders_next_attempt", "reminders", ["next_attempt"])


def downgrade():
    op.drop_table("reminders")
