"""Read one HTML page: visible text, emails, phones, social links and the links to follow.

`parse_page()` turns a page into a `ParsedPage`. The crawler (`crawler.py`) calls
`pick_links()` on the home page to choose which other pages to visit;
`app.services.enrichment` combines every page's `ParsedPage` into the lead's enrichment.

Emails and phones come from `mailto:` / `tel:` links, the page's schema.org JSON-LD
(data the business publishes for search engines) and the visible text. Emails a site
hides from bots (e.g. Cloudflare's email obfuscation) are left hidden on purpose.

Everything found here is a candidate, not a fact: phones are checked with the
`phonenumbers` library in Phase 5's extract node, and the LLM may only use emails
that are in this list (the traceability check).
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

# Tags whose text a visitor never sees.
HIDDEN_TAGS = ["script", "style", "noscript", "template", "svg", "head"]

# Bounded lengths (an address allows 64 + 253 chars): an unbounded `+` makes the regex
# crawl very slowly over a page with one very long word.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")
# Looks like an email but is a file name ("logo@2x.png") or a tracking/placeholder address.
NOT_EMAIL_ENDINGS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
JUNK_EMAIL_DOMAINS = ("example.com", "example.org", "domain.com", "sentry.io", "wixpress.com")

# Common written phone shapes: "(512) 474-7575", "+1 512.474.7575", "01-4412345", "020 7946 0958".
PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?\(?\d{1,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\w)"
)
PHONE_MIN_DIGITS = 9
PHONE_MAX_DIGITS = 15

# host (without "www." / "m.") -> network name
SOCIAL_HOSTS = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "x.com": "x",
    "twitter.com": "x",
}
# Share buttons point at the network, not at the business's own page.
SHARE_PATH_WORDS = ("sharer", "share", "intent", "dialog")

# Pages worth a visit, best first. A link matches when its URL or its text has the word.
LINK_KEYWORDS = ("contact", "about", "team", "service")
# Links to files we would skip anyway (the crawler also checks the Content-Type).
FILE_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".doc", ".docx")

# An <iframe> whose address has one of these words is an embedded contact/booking form
# (e.g. "https://www.doctible.com/forms/123", "https://form.jotform.com/456").
FORM_IFRAME_WORDS = {"form", "forms", "book", "booking", "appointment", "appointments", "schedule"}


@dataclass
class Link:
    url: str  # absolute, without the #fragment
    text: str


@dataclass
class ParsedPage:
    url: str
    text: str = ""
    links: list[Link] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)  # mailto: links, then JSON-LD, then text
    phones: list[str] = field(default_factory=list)  # tel: links, then JSON-LD, then text
    has_mobile_viewport: bool = False
    has_form: bool = False


def parse_page(html: str, url: str) -> ParsedPage:
    """Everything enrichment needs from one page. `url` is where the page really came from."""
    soup = BeautifulSoup(html, "html.parser")
    page = ParsedPage(url=url)
    # Read <head> and <script> before the visible-text step removes them.
    page.has_mobile_viewport = has_mobile_viewport(soup)
    page.has_form = has_contact_form(soup)
    page.links = page_links(soup, url)
    data_emails, data_phones = structured_data_contacts(soup)

    hrefs = [link.url for link in page.links]
    mailto = [href for href in hrefs if href.lower().startswith("mailto:")]
    tel = [unquote(href[4:]).strip() for href in hrefs if href.lower().startswith("tel:")]

    page.text = visible_text(soup)
    page.emails = unique(
        [*mailto_emails(mailto), *find_emails(" ".join(data_emails)), *find_emails(page.text)]
    )
    page.phones = unique_phones([*tel, *data_phones, *find_phones(page.text)])
    return page


def visible_text(soup: BeautifulSoup) -> str:
    """The page's text as a visitor reads it, with whitespace collapsed. Changes `soup`."""
    for tag in soup(HIDDEN_TAGS):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def page_links(soup: BeautifulSoup, base_url: str) -> list[Link]:
    links = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        links.append(Link(url=without_fragment(absolute), text=anchor.get_text(" ", strip=True)))
    return links


def has_mobile_viewport(soup: BeautifulSoup) -> bool:
    """True when the page has `<meta name="viewport" content="width=device-width ...">`."""
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() == "viewport":
            return "width=device-width" in str(meta.get("content", "")).replace(" ", "").lower()
    return False


def has_contact_form(soup: BeautifulSoup) -> bool:
    """True when the page has a contact or booking form.

    That's a <form> that takes a message, an email or a phone (a search box alone is
    not one), or an <iframe> that embeds a form/booking widget.
    """
    for form in soup.find_all("form"):
        if form.find("textarea"):
            return True
        input_types = {str(tag.get("type", "")).lower() for tag in form.find_all("input")}
        if input_types & {"email", "tel"}:
            return True
    for iframe in soup.find_all("iframe", src=True):
        parts = urlsplit(str(iframe["src"]).lower())
        # Whole words only: "platform.twitter.com" has "form" inside a word, not as one.
        if FORM_IFRAME_WORDS & set(re.split(r"[^a-z]+", f"{parts.hostname} {parts.path}")):
            return True
    return False


def structured_data_contacts(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    """The "email" and "telephone" values in the page's schema.org JSON-LD blocks.

    Many sites describe the business for search engines in
    `<script type="application/ld+json">{"@type": "Dentist", "telephone": ...}</script>`.
    A block that isn't valid JSON is skipped.
    """
    emails: list[str] = []
    phones: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.get_text())
        except ValueError:
            continue
        for key, value in json_strings(data, {"email", "telephone"}):
            if key == "email":
                emails.append(value.removeprefix("mailto:"))
            else:
                phones.append(value)
    return emails, phones


def json_strings(data: Any, keys: set[str]) -> Iterator[tuple[str, str]]:
    """Every (key, string value) pair for the given keys, at any depth of the JSON."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, str):
                yield key, value
            else:
                yield from json_strings(value, keys)
    elif isinstance(data, list):
        for item in data:
            yield from json_strings(item, keys)


def mailto_emails(hrefs: list[str]) -> list[str]:
    """'mailto:Info@Smile.com?subject=Hi' -> ['info@smile.com'] (a link can list several)."""
    emails = []
    for href in hrefs:
        addresses = unquote(href[len("mailto:") :]).split("?")[0]
        emails.extend(address.strip() for address in addresses.split(","))
    return [email.lower() for email in emails if is_real_email(email)]


def find_emails(text: str) -> list[str]:
    return [email.lower() for email in EMAIL_RE.findall(text) if is_real_email(email)]


def is_real_email(email: str) -> bool:
    email = email.lower()
    if not EMAIL_RE.fullmatch(email) or email.endswith(NOT_EMAIL_ENDINGS):
        return False
    domain = email.rsplit("@", 1)[1]
    return not any(domain == junk or domain.endswith(f".{junk}") for junk in JUNK_EMAIL_DOMAINS)


def find_phones(text: str) -> list[str]:
    return [match for match in PHONE_RE.findall(text) if phone_digit_count_ok(match)]


def phone_digit_count_ok(phone: str) -> bool:
    return PHONE_MIN_DIGITS <= len(re.sub(r"\D", "", phone)) <= PHONE_MAX_DIGITS


def unique_phones(phones: list[str]) -> list[str]:
    """Keep the first spelling of each number.

    Two spellings are the same number when their digits match, or when one is the
    other with a country code in front ("+1-512-555-0142" and "(512) 555-0142").
    """
    seen: list[str] = []
    result = []
    for phone in phones:
        digits = re.sub(r"\D", "", phone)
        if not phone_digit_count_ok(phone):
            continue
        if any(digits.endswith(other) or other.endswith(digits) for other in seen):
            continue
        seen.append(digits)
        result.append(phone)
    return result


def find_socials(links: list[Link]) -> dict[str, str]:
    """{"facebook": url, "instagram": url, ...}: the first profile link per network."""
    socials: dict[str, str] = {}
    for link in links:
        parts = urlsplit(link.url)
        host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
        network = SOCIAL_HOSTS.get(host)
        path = parts.path.strip("/").lower()
        if not network or network in socials or not path:
            continue
        if any(word in path for word in SHARE_PATH_WORDS):
            continue
        socials[network] = link.url
    return socials


def pick_links(page: ParsedPage, limit: int) -> list[str]:
    """Up to `limit` pages of the same site to visit next: one per keyword, in keyword order."""
    candidates = [link for link in page.links if is_page_on_site(link.url, page.url)]

    chosen: list[str] = []
    for keyword in LINK_KEYWORDS:
        if len(chosen) >= limit:
            break
        for link in candidates:
            haystack = f"{urlsplit(link.url).path} {link.text}".lower()
            if keyword in haystack and link.url not in chosen:
                chosen.append(link.url)
                break
    return chosen


def is_page_on_site(url: str, home_url: str) -> bool:
    """True for another web page (not a file) on the same site as `home_url`."""
    parts = urlsplit(url)
    return (
        parts.scheme in ("http", "https")
        and not parts.path.lower().endswith(FILE_EXTENSIONS)
        and site_host(url) == site_host(home_url)
        and not same_page(url, home_url)
    )


def site_host(url: str) -> str:
    """'https://www.Smile.com/x' -> 'smile.com', so www and no-www count as one site."""
    return (urlsplit(url).hostname or "").removeprefix("www.")


def same_page(a: str, b: str) -> bool:
    """True for the same page, ignoring http/https, www and a trailing slash."""

    def key(url: str) -> tuple[str, str, str]:
        parts = urlsplit(url)
        return site_host(url), parts.path.rstrip("/"), parts.query

    return key(a) == key(b)


def without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def unique(items: list[str]) -> list[str]:
    """Drop repeats, keep the order."""
    return list(dict.fromkeys(items))
