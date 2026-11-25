"""Add worker lease columns and index for multi-worker duplicate prevention."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_worker_leases"
down_revision = "0001_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("tasks")}

    dialect = bind.dialect.name
    if "lease_owner" not in columns:
        op.add_column("tasks", sa.Column("lease_owner", sa.String(length=64), nullable=True))
    if "lease_expires_at" not in columns:
        op.add_column(
            "tasks",
            sa.Column(
                "lease_expires_at",
                sa.DateTime(timezone=dialect == "postgresql"),
                nullable=True,
            ),
        )

    indexes = {index["name"] for index in inspect(bind).get_indexes("tasks")}
    if "ix_tasks_lease_owner" not in indexes:
        op.create_index("ix_tasks_lease_owner", "tasks", ["lease_owner"], unique=False)


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
