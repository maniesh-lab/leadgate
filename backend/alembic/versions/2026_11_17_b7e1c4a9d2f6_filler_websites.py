"""leads: blank the filler websites saved before ingestion learnt to skip them.

A business's listing can hold template text instead of its site ("https://yourwebsite.com/").
Ingestion now saves those as no website (`app.services.ingestion.FILLER_DOMAINS`, copied
here so this migration never changes). For the leads saved before, this blanks the website
and what the crawler found there, since it came from a stranger's site. Nothing to undo.

Revision ID: b7e1c4a9d2f6
Revises: 4e8b2d6f1a93
Create Date: 2026-11-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e1c4a9d2f6"
down_revision: str | Sequence[str] | None = "4e8b2d6f1a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILLER_DOMAINS = (
    "example.com",
    "example.net",
    "example.org",
    "domain.com",
    "website.com",
    "mysite.com",
    "mywebsite.com",
    "yourdomain.com",
    "yoursite.com",
    "yourwebsite.com",
    "yourcompany.com",
    "yourbusiness.com",
)


def upgrade() -> None:
    leads = sa.table(
        "leads",
        sa.column("website"),
        sa.column("website_domain"),
        sa.column("enrichment"),
        sa.column("enriched_at"),
    )
    op.execute(
        leads.update()
        .where(leads.c.website_domain.in_(FILLER_DOMAINS))
        .values(website=None, website_domain=None, enrichment=None, enriched_at=None)
    )


def downgrade() -> None:
    pass  # the blanked websites were filler: there's nothing to put back
