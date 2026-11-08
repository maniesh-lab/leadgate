"""scoring_rubrics.description: who a rubric suits, shown beside a lead's score.

Adds a nullable text column and writes the seeded text (`app.db.seed.RUBRIC_DESCRIPTIONS`,
copied here so this migration never changes) into the two built-in rubrics, every
version, where it's still empty. Rubrics an admin made stay without one until edited.

Revision ID: 9a3c5e7f2b14
Revises: 7d4e2b9c1a60
Create Date: 2026-11-08 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a3c5e7f2b14"
down_revision: str | Sequence[str] | None = "7d4e2b9c1a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESCRIPTIONS = {
    "general-b2b": (
        "For general sales outreach: favours businesses you can reach (phone, email, "
        "website) that look well run (good rating, many reviews, open)."
    ),
    "web-agency": (
        "For selling websites: favours well-reviewed businesses with no website or an "
        "old one. A lead outside the campaign's business type scores 0."
    ),
}


def upgrade() -> None:
    op.add_column("scoring_rubrics", sa.Column("description", sa.Text(), nullable=True))
    rubrics = sa.table("scoring_rubrics", sa.column("name"), sa.column("description"))
    for name, text in DESCRIPTIONS.items():
        op.execute(
            rubrics.update()
            .where(rubrics.c.name == name, rubrics.c.description.is_(None))
            .values(description=text)
        )


def downgrade() -> None:
    op.drop_column("scoring_rubrics", "description")
