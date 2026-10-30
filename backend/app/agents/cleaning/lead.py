"""Deterministic cleaning of one lead, before the LLM sees it.

`clean_lead()` turns the lead as collected (`state["raw"]`) plus its website
enrichment into the `cleaned` dict: trimmed text, phones in E.164, candidate emails
(the source's first, then the website's, best first), and the source's own rating,
reviews and status. Plain rules, no LLM, so the same input always cleans the same way.

Also here: what the traceability checks look in.
- `campaign_geo()`: the campaign's place (location, country code, the geocoded name)
- `source_text()`: every value in the source's own data plus the campaign's place,
  as one string. The LLM's area / city / country must appear in it.
- `mentions()`: "does this text contain this value" (case and spacing don't matter)

Called by the `extract` node.
"""

import json
import re
from typing import Any

from app.agents.categories import niche_category
from app.agents.cleaning.phones import find_phones
from app.db.models import Campaign, Lead


def lead_snapshot(lead: Lead) -> dict[str, Any]:
    """The lead as the agents first see it: its common fields plus the source's own data."""
    return {
        "source": lead.source,
        "source_ref": lead.source_ref,
        "name": lead.name,
        "phone": lead.phone,
        "email": lead.email,
        "website": lead.website,
        "address": lead.address,
        "category": lead.category,
        "rating": lead.rating,
        "review_count": lead.review_count,
        "business_status": lead.business_status,
        # The source's untouched answer (OSM tags, the Google place, the CSV row).
        "data": lead.raw,
    }


def campaign_geo(campaign: Campaign) -> dict[str, Any]:
    """Where the campaign looks: its location text, country code and geocoded place name.

    The country code comes from the OSM geocode (cached by the `osm` source) or a
    `country` in the campaign's source options; None if neither is there.
    """
    options = campaign.source_options or {}
    area = options.get("osm_area") or {}
    country = area.get("country_code") or options.get("country")
    return {
        "location": campaign.location,
        "country_code": country.upper() if country else None,
        "place": area.get("display_name"),
    }


def clean_lead(
    raw: dict[str, Any], enrichment: dict[str, Any], country: str | None
) -> dict[str, Any]:
    """The `cleaned` dict for one lead (see the module docstring)."""
    source_phones = find_phones(raw.get("phone"), country)
    site_phones = [
        phone
        for text in enrichment.get("phones", [])
        for phone in find_phones(text, country)
        if phone not in source_phones
    ]
    phones = unique([*source_phones, *site_phones])
    emails = unique(
        [email.casefold() for email in [raw.get("email"), *enrichment.get("emails", [])] if email]
    )
    return {
        "name": tidy(raw.get("name")) or "",
        # The source's phone first: the business gave it to the directory on purpose.
        "phone": phones[0] if phones else None,
        "phones": phones,
        "emails": emails,
        "website": tidy(raw.get("website")),
        "address": tidy(raw.get("address")),
        "rating": raw.get("rating"),
        "review_count": raw.get("review_count"),
        "business_status": raw.get("business_status"),
        "category_guess": niche_category(raw.get("category")),
        "country_code": country,
    }


def source_text(raw: dict[str, Any], geo: dict[str, Any]) -> str:
    """Everything the source said about the lead, plus the campaign's place, as one string."""
    parts = [raw.get("address"), geo.get("location"), geo.get("place"), geo.get("country_code")]
    parts += values_in(raw.get("data"))
    return " | ".join(str(part) for part in parts if part)


def values_in(data: Any) -> list[str]:
    """Every string and number inside nested dicts and lists."""
    if isinstance(data, dict):
        return [value for item in data.values() for value in values_in(item)]
    if isinstance(data, list):
        return [value for item in data for value in values_in(item)]
    if isinstance(data, str | int | float) and not isinstance(data, bool):
        return [str(data)]
    return []


def find_value(data: Any, names: tuple[str, ...]) -> str | None:
    """The first text value under one of `names`, looking inside nested dicts.

    E.g. find_value(osm_raw, ("addr:city", "city")) finds raw["tags"]["addr:city"].
    """
    if not isinstance(data, dict):
        return None
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return tidy(value)
    for value in data.values():
        found = find_value(value, names)
        if found:
            return found
    return None


def mentions(text: str, value: str) -> bool:
    """True when `value` appears in `text` as whole words, ignoring case and spacing."""
    wanted = squash(value)
    if not wanted:
        return False
    return re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", squash(text)) is not None


def squash(text: str) -> str:
    return " ".join(text.casefold().split())


def tidy(text: str | None) -> str | None:
    """Trim and collapse spaces; empty becomes None."""
    if text is None:
        return None
    return " ".join(str(text).split()) or None


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def short_json(data: Any, limit: int) -> str:
    """`data` as compact JSON, cut at `limit` characters (for prompts)."""
    return json.dumps(data, ensure_ascii=False, default=str)[:limit]
