"""users.token_version: end a user's older login tokens (a password change bumps it).

Every login token carries the version it was made with; `app.api.deps` refuses a token
whose version is older than the user's. Existing users start at 0, the same as tokens
made before this column existed, so nobody is logged out by the upgrade.

Revision ID: 8b1f2c4d5e6a
Revises: 3ec64e25a3e3
Create Date: 2026-10-30 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b1f2c4d5e6a"
down_revision: str | Sequence[str] | None = "3ec64e25a3e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
