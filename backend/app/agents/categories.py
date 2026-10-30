"""The fixed list of lead categories, and how a campaign's niche maps onto it.

The `extract` node must pick a category from CATEGORIES (or "other"), so leads from
every source end up with the same few words. The list is the niches the OSM source
knows (`app.sources.osm.NICHE_TAGS`), so a campaign niche, an OSM tag and a lead's
category all speak the same language. Aliases ("vet", "realtor") come from there too.
"""

from app.sources.osm import NICHE_ALIASES, NICHE_TAGS

OTHER = "other"
CATEGORIES = [*NICHE_TAGS, OTHER]


def niche_category(text: str | None) -> str | None:
    """The category a niche or hint names ("Dentist", "vet" -> "veterinarian"), else None."""
    if not text:
        return None
    word = " ".join(text.casefold().replace("_", " ").split())
    word = NICHE_ALIASES.get(word, word)
    return word if word in NICHE_TAGS else None


def campaign_category(niche: str) -> str:
    """The category a campaign's leads should have; "other" for a niche not in the list."""
    return niche_category(niche) or OTHER
