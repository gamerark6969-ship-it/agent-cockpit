"""Adopt legacy project tables that predate general chat threads."""

import sqlalchemy as sa
from alembic import op

revision = "0007_project_chat_schema"
down_revision = "0006_task_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"]:column for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "kind" not in columns or not columns["repo_url"]["nullable"]:
        with op.batch_alter_table("projects") as batch:
            if "kind" not in columns:
                batch.add_column(sa.Column("kind",sa.Text(),nullable=False,server_default="repo"))
            if not columns["repo_url"]["nullable"]:
                batch.alter_column("repo_url",existing_type=sa.Text(),nullable=True)


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
