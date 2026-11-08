"""The fixed list of lead categories, and how a campaign's niche maps onto it.

The `extract` node must pick a category from CATEGORIES (or "other"), so leads from
every source end up with the same words. The list is the niches the OSM source knows
(`app.sources.osm.NICHE_TAGS`), so a campaign niche, an OSM tag and a lead's category all
speak the same language. Aliases ("vet", "realtor") come from there too.

What counts as the right category for a campaign (`category_matches()`):
- the same category, or a niche off the list ("web design": nothing to compare, so every
  lead matches; the `check` node then says the category wasn't checked)
- a general doctor or clinic for a specialist campaign, and the reverse
- a close neighbour (`NEIGHBOURS`): businesses the sources file under either word, like
  a guest house and a hotel, or a cafe and a restaurant (Boss's call, 2026-11-08: these
  used to count as wrong, and on `web-agency` a wrong category zeroes the score)

A beauty salon is still wrong for a dermatologist campaign: a "skin and hair clinic" can
be either, so the model picks from the name and the website and a reviewer decides.

`prompt_categories()` gives the extract prompt the campaign's sector (`SECTORS`: health,
food, trades, ...) plus the source's hint instead of all 120 words, which keeps each call
over 1,000 characters shorter. The whole sector, not just the words that count as right,
so the model can still name a wrong business in it (a cardiologist on a dermatologist
search); a business from another sector becomes "other", which is wrong for the campaign
too.
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
# Close neighbours: a lead filed under one word of a group is right for a campaign that
# wants another word of it. A word may be in more than one group.
NEIGHBOURS = [
    {"hotel", "motel", "hostel", "guest house"},
    {"restaurant", "cafe", "fast food"},
    {"bar", "nightclub", "brewery"},
    {"hair salon", "beauty salon", "nail salon"},
    {"beauty salon", "spa", "massage"},
    {"physiotherapist", "chiropractor"},
    {"psychiatrist", "psychotherapist"},
    {"accountant", "tax advisor", "financial advisor"},
    {"laundry", "dry cleaner"},
    {"kindergarten", "childcare"},
    {"college", "university"},
    {"supermarket", "convenience store"},
    {"electronics store", "computer shop", "mobile phone shop"},
    {"car repair", "tire shop"},
    {"car dealer", "motorcycle dealer"},
]
# Every category in exactly one sector (a test checks it), in NICHE_TAGS order.
SECTORS = [
    [
        "dentist",
        "doctor",
        "clinic",
        "hospital",
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
        "chiropractor",
        "physiotherapist",
        "psychotherapist",
        "medical laboratory",
        "pharmacy",
        "optician",
        "nursing home",
        "veterinarian",
    ],
    ["hair salon", "beauty salon", "nail salon", "spa", "massage", "tattoo studio"],
    [
        "restaurant",
        "cafe",
        "bar",
        "fast food",
        "bakery",
        "ice cream shop",
        "butcher",
        "nightclub",
        "brewery",
        "caterer",
    ],
    ["gym", "yoga studio", "martial arts school", "dance studio", "sports centre", "golf course"],
    [
        "real estate agent",
        "lawyer",
        "accountant",
        "tax advisor",
        "insurance agent",
        "notary",
        "financial advisor",
        "consultant",
        "it company",
        "advertising agency",
        "architect",
        "engineer",
        "surveyor",
        "employment agency",
        "logistics company",
        "travel agency",
        "coworking space",
        "bank",
    ],
    [
        "plumber",
        "electrician",
        "carpenter",
        "painter",
        "roofer",
        "hvac contractor",
        "locksmith",
        "landscaper",
        "builder",
        "flooring contractor",
        "window installer",
    ],
    ["photographer", "tailor", "laundry", "dry cleaner", "funeral home"],
    [
        "car repair",
        "car dealer",
        "car wash",
        "car rental",
        "tire shop",
        "motorcycle dealer",
        "driving school",
        "gas station",
    ],
    [
        "supermarket",
        "convenience store",
        "liquor store",
        "clothing store",
        "shoe store",
        "jewelry store",
        "furniture store",
        "hardware store",
        "electronics store",
        "mobile phone shop",
        "computer shop",
        "bookstore",
        "florist",
        "toy store",
        "bicycle shop",
        "sporting goods store",
        "cosmetics store",
        "pet store",
        "pet groomer",
    ],
    [
        "school",
        "kindergarten",
        "childcare",
        "college",
        "university",
        "language school",
        "music school",
        "tutoring center",
    ],
    ["hotel", "motel", "hostel", "guest house", "event venue"],
]


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
    general one, but one specialist doesn't match another. Close neighbours match.
    """
    return wanted == OTHER or category == wanted or category in related(wanted)


def related(wanted: str) -> set[str]:
    """The other categories that count as right for a campaign wanting `wanted`."""
    found = set().union(*(group for group in NEIGHBOURS if wanted in group))
    if wanted in GENERAL_MEDICAL:
        found |= GENERAL_MEDICAL | MEDICAL_SPECIALISTS
    elif wanted in MEDICAL_SPECIALISTS:
        found |= GENERAL_MEDICAL
    return found - {wanted}


def prompt_categories(wanted: str, guess: str | None) -> list[str]:
    """The categories the extract prompt offers: the campaign's sector, the source's hint
    and "other". A niche off the list gets them all, since there's nothing to narrow it to."""
    if wanted == OTHER:
        return CATEGORIES
    words = {wanted, *related(wanted)}.union(*(sector for sector in SECTORS if wanted in sector))
    if guess in CATEGORIES:
        words.add(guess)
    return [category for category in CATEGORIES if category in words or category == OTHER]
