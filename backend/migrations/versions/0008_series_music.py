"""Store a selected music track with each series in the shared database."""

import sqlalchemy as sa
from alembic import op

revision = "0008_series_music"
down_revision = "0007_project_chat_schema"
branch_labels = None
depends_on = None


def upgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("automation_series")}
    for name, kind in (("music_name", sa.String(160)), ("music_data", sa.LargeBinary())):
        if name not in existing:
            op.add_column("automation_series", sa.Column(name, kind, nullable=True))


def downgrade():
    with op.batch_alter_table("automation_series") as batch:
        batch.drop_column("music_data")
        batch.drop_column("music_name")
