"""csv_uploads: CSV files admins upload in the dashboard (Phase 14).

The file is stored in the database instead of on a disk, so every worker reads the same
copy. A campaign points to one with `source_options["csv_upload_id"]`.

Revision ID: 5c9e1a7b3d20
Revises: 8b1f2c4d5e6a
Create Date: 2026-11-02 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5c9e1a7b3d20"
down_revision: str | Sequence[str] | None = "8b1f2c4d5e6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "csv_uploads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column(
            "columns",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_csv_uploads_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_csv_uploads")),
    )


def downgrade() -> None:
    op.drop_table("csv_uploads")
