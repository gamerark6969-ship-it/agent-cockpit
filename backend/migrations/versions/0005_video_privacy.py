"""Allow private commissioning uploads and explicit public publishing."""

import sqlalchemy as sa
from alembic import op

revision = "0005_video_privacy"
down_revision = "0004_video_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("automation_series")}
    if "privacy" not in columns:
        op.add_column("automation_series",sa.Column("privacy",sa.String(16),nullable=False,server_default="public"))


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
