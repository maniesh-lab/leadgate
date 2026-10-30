"""Bodies for the n8n webhook (`POST /api/webhooks/n8n/runs`)."""

from typing import Any, Self

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from app.db.models import RunStatus
from app.schemas.campaigns import strip_text

# Leads one webhook call can bring (they ride in the start_run job's payload).
MAX_INLINE_LEADS = 500


class N8nRunRequest(BaseModel):
    """Which campaign to run: `campaign_id`, or `niche` + `location` (found or created)."""

    campaign_id: int | None = Field(default=None, examples=[1])
    niche: str | None = Field(default=None, min_length=1, max_length=100, examples=["dentist"])
    location: str | None = Field(
        default=None, min_length=1, max_length=200, examples=["Austin, TX"]
    )
    # This run's leads instead of the campaign's sources: objects with the lead field names
    # (name, phone, email, website, address, lat, lng, category, rating, review_count,
    # business_status). Bad ones are skipped and counted in the run's stats.
    leads: list[dict[str, Any]] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INLINE_LEADS,
        examples=[[{"name": "Smile Austin Dental", "phone": "(512) 555-0142"}]],
    )
    # An n8n Webhook node: gets the run's summary when it completes or fails.
    callback_url: AnyHttpUrl | None = Field(
        default=None, examples=["http://n8n:5678/webhook/leadgate-results"]
    )

    _strip = field_validator("niche", "location")(strip_text)

    @model_validator(mode="after")
    def campaign_or_niche_and_location(self) -> Self:
        if self.campaign_id is None and not (self.niche and self.location):
            raise ValueError("send campaign_id, or niche and location")
        return self


class N8nRunAccepted(BaseModel):
    run_id: int
    campaign_id: int
    status: RunStatus
