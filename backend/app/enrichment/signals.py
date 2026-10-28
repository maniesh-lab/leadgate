"""Simple signals about how modern a business's website is.

The web-agency rubric (PLAN §5) scores leads with these: an old copyright year or a
site without https / a mobile layout means the business may want a new website.
`app.services.enrichment` calls `site_signals()` and stores the result in
`lead.enrichment["signals"]`, where the rubric reads it as `signals.<name>`.

A signal is None when we couldn't look (no website, or the home page didn't load):
"unknown" must never count as "bad website" in the score.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from app.enrichment.extract_html import ParsedPage

# "© 2019", "Copyright 2015 - 2024", "(c) 2012–2018": the year we want is the last one.
COPYRIGHT_RE = re.compile(
    r"(?:copyright|©|\(c\))[\s©]*(?:(?:19|20)\d{2}\s*[-–—]\s*)?((?:19|20)\d{2})", re.IGNORECASE
)
OLDEST_PLAUSIBLE_YEAR = 1990


def site_signals(
    home: ParsedPage | None, pages: list[ParsedPage], current_year: int
) -> dict[str, Any]:
    """The signals for one website.

    `home` is the parsed home page (None when it didn't load); `pages` are all pages
    that loaded, home included.
    """
    if home is None:
        return {
            "page_load_ok": False,
            "https": None,
            "mobile_viewport": None,
            "copyright_year": None,
            "has_form": None,
            "https_and_mobile": None,
        }

    https = urlsplit(home.url).scheme == "https"
    return {
        "page_load_ok": True,
        "https": https,
        "mobile_viewport": home.has_mobile_viewport,
        "copyright_year": latest_copyright_year([page.text for page in pages], current_year),
        "has_form": any(page.has_form for page in pages),
        # One signal for the rubric's "no https or no mobile viewport" rule (its ops have no "or").
        "https_and_mobile": https and home.has_mobile_viewport,
    }


def no_site_signals() -> dict[str, Any]:
    """The signals for a lead with no website: nothing to look at."""
    signals = site_signals(None, [], 0)
    signals["page_load_ok"] = None
    return signals


def latest_copyright_year(texts: list[str], current_year: int) -> int | None:
    """The newest copyright year on any page, ignoring years that can't be right."""
    years = [
        int(year)
        for text in texts
        for year in COPYRIGHT_RE.findall(text)
        if OLDEST_PLAUSIBLE_YEAR <= int(year) <= current_year
    ]
    return max(years, default=None)
