"""Store the Web application OAuth client separately from channel authorization."""

import sqlalchemy as sa
from alembic import op

revision = "0011_youtube_client"
down_revision = "0010_chart_music"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("automation_youtube_client"):
        op.create_table("automation_youtube_client",
                        sa.Column("id", sa.Integer(), primary_key=True),
                        sa.Column("config_enc", sa.Text(), nullable=False))


def downgrade():
    op.drop_table("automation_youtube_client")
