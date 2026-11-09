"""Password reset links and two-factor login.

`password_reset_links`: one-time links an admin or the CLI makes (only the token's hash is
kept). `users` gets the two-factor columns: the authenticator secret, whether it's on, and
the last code's time step (so a code works once).

Revision ID: 4e8b2d6f1a93
Revises: 9a3c5e7f2b14
Create Date: 2026-11-09 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4e8b2d6f1a93"
down_revision: str | Sequence[str] | None = "9a3c5e7f2b14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.create_table(
        "password_reset_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_links_token_hash")),
    )
    op.create_index(
        op.f("ix_password_reset_links_user_id"), "password_reset_links", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_password_reset_links_user_id"), table_name="password_reset_links")
    op.drop_table("password_reset_links")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")
