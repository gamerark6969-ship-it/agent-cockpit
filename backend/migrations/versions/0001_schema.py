"""Create or adopt the application schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.models import Base

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names()) - {"alembic_version"}

    if not tables:
        Base.metadata.create_all(bind=bind)
        return

    if "tasks" not in tables:
        raise RuntimeError("existing database is not a recognized application schema")

    columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "pa_history" not in columns:
        op.add_column("tasks", sa.Column("pa_history", sa.JSON(), nullable=True))

    # Existing installations may predate later tables. SQLAlchemy only creates
    # missing tables here; all column changes remain explicit above.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    raise RuntimeError("schema downgrade is intentionally unsupported")
