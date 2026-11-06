"""The fixed list of lead categories, and how a campaign's niche maps onto it.

The `extract` node must pick a category from CATEGORIES (or "other"), so leads from
every source end up with the same words. The list is the niches the OSM source knows
(`app.sources.osm.NICHE_TAGS`), so a campaign niche, an OSM tag and a lead's category all
speak the same language. Aliases ("vet", "realtor") come from there too.
"""

from app.sources.osm import NICHE_ALIASES, NICHE_TAGS

OTHER = "other"
CATEGORIES = [*NICHE_TAGS, OTHER]

# A lead filed under one of these may still be the specialist a campaign wants: a skin
# clinic is often just "doctor" or "clinic" in the source data.
GENERAL_MEDICAL = {"doctor", "clinic"}
MEDICAL_SPECIALISTS = {
    "dermatologist",
    "pediatrician",
    "gynecologist",
    "ophthalmologist",
    "orthopedist",
    "cardiologist",
    "neurologist",
    "psychiatrist",
    "ent specialist",
    "urologist",
    "oncologist",
}


def niche_category(text: str | None) -> str | None:
    """The category a niche or hint names ("Dentists", "vet" -> ...), else None."""
    if not text:
        return None
    word = " ".join(text.casefold().replace("_", " ").split())
    word = NICHE_ALIASES.get(word, word)
    if word not in NICHE_TAGS and word.endswith("s"):
        word = NICHE_ALIASES.get(word[:-1], word[:-1])
    return word if word in NICHE_TAGS else None


def campaign_category(niche: str) -> str:
    """The category a campaign's leads should have; "other" for a niche not in the list.

    The `check` node treats "other" as "nothing to compare": those leads always match.
    """
    return niche_category(niche) or OTHER


def category_matches(wanted: str, category: str | None) -> bool:
    """Is a lead of this category right for a campaign wanting `wanted`?

    General doctors and clinics match a specialist campaign, and specialists match a
    general one, but one specialist doesn't match another.
    """
    if wanted == OTHER or category == wanted:
        return True
    if category in GENERAL_MEDICAL:
        return wanted in GENERAL_MEDICAL | MEDICAL_SPECIALISTS
    return wanted in GENERAL_MEDICAL and category in MEDICAL_SPECIALISTS
