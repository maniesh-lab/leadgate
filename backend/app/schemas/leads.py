"""Bodies for `/api/leads`, plus the short lead card the run and approval answers embed."""

from app.db.models import LeadStatus
from app.schemas.common import FromRow


class LeadSummary(FromRow):
    """The fields a reviewer looks at first."""

    id: int
    name: str
    phone: str | None
    email: str | None
    website: str | None
    address: str | None
    area: str | None
    city: str | None
    category: str | None
    rating: float | None
    review_count: int | None
    score: int | None
    status: LeadStatus
    opt_out: bool
