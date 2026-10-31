"""Fetch a few pages of a business's own website, politely.

`Crawler.crawl(website)` fetches the home page, then up to `max_pages - 1` pages
it links to that look like contact / about / team / services pages
(`extract_html.pick_links`). `app.services.enrichment` calls it once per lead.

The rules (PLAN §4, plus 10c's public-addresses-only):
- robots.txt is read once per site and obeyed (RFC 9309: a 4xx answer means no
  rules, a 5xx or no answer means stay out). A `Crawl-delay` there makes us wait longer.
- at least 1 s between two requests to the same domain (robots.txt included)
- a timeout on every request; at most 3 redirects. A page's redirects are followed by
  hand, so each hop is checked against robots.txt and waits its turn; the robots.txt
  request itself lets httpx2 follow its (max 3) redirects.
- only HTML pages are read (a PDF or an image is skipped), and only the first 2 MB
- no logins, no CAPTCHAs, no proxies
- only public internet addresses: a request (or a redirect hop) to localhost, a private
  network (10.x, 192.168.x, the compose services) or a cloud metadata address is refused
  before it's sent (`public_only`), since a lead's website can come from any CSV or n8n.

A page that fails (timeout, 404, not HTML, blocked) becomes a `FetchedPage` with an
`error`; the crawler never raises for network problems, so one bad site can't stop a batch.
"""

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx2
import structlog

from app.enrichment.extract_html import ParsedPage, parse_page, pick_links, site_host

log = structlog.get_logger(__name__)

TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_PAGE_BYTES = 2_000_000
# RFC 9309 asks crawlers to read at least 500 KiB of a robots.txt.
MAX_ROBOTS_CHARS = 500_000
HTML_TYPES = ("text/html", "application/xhtml+xml")


@dataclass
class FetchedPage:
    """One page we tried to fetch."""

    url: str  # the URL we asked for
    final_url: str  # where we ended up after redirects
    status: int | None = None  # HTTP status of the last answer; None = no answer
    error: str | None = None  # why there is no page: robots.txt, timeout, HTTP 404, not HTML, ...
    parsed: ParsedPage | None = None  # set only when the page loaded

    def summary(self) -> dict[str, Any]:
        """What gets stored in `lead.enrichment["pages"]` (no HTML)."""
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "error": self.error,
        }


class PrivateAddressError(httpx2.RequestError):
    """A request to an address that isn't on the public internet; it's never sent."""


def resolve_host(host: str) -> list[str]:
    """Every IP address a host name points to (none if it doesn't resolve)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return [str(info[4][0]) for info in infos]


def is_public(address: str) -> bool:
    """False for loopback, private, link-local (cloud metadata), CGNAT, reserved, ..."""
    ip = ipaddress.ip_address(address.split("%")[0])  # drop an IPv6 zone like "%eth0"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is 127.0.0.1
    return ip.is_global


def public_only(resolve: Callable[[str], list[str]] = resolve_host) -> Callable[..., None]:
    """An httpx request hook that refuses any request to a non-public address.

    httpx runs it before every request, redirect hops included. A host name counts as
    public only if every address it resolves to is. One it can't resolve is let through:
    httpx then fails to connect to it anyway. (The address is looked up again when httpx
    connects, so a DNS server that answers differently the second time could still slip
    through; see PROGRESS Known issues.)
    """

    def check(request: httpx2.Request) -> None:
        host = request.url.host
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:  # a name, not an IP
            addresses = resolve(host)
        if not all(is_public(address) for address in addresses):
            raise PrivateAddressError(f"{host} is not a public address", request=request)

    return check


def make_crawler_client(
    user_agent: str, resolve: Callable[[str], list[str]] = resolve_host
) -> httpx2.Client:
    """The HTTP client for business websites (tests pass a mocked one instead)."""
    return httpx2.Client(
        headers={"User-Agent": user_agent},
        timeout=TIMEOUT_SECONDS,
        # Only robots.txt requests follow redirects by themselves; pages do it in `fetch()`.
        max_redirects=MAX_REDIRECTS,
        event_hooks={"request": [public_only(resolve)]},
    )


def start_url(website: str) -> str:
    """'www.smile.com' -> 'http://www.smile.com' (sites usually redirect to https themselves)."""
    website = website.strip()
    return website if "://" in website else f"http://{website}"


class Crawler:
    def __init__(
        self,
        client: httpx2.Client,
        user_agent: str,
        max_pages: int = 4,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.user_agent = user_agent
        self.max_pages = max_pages
        # Tests pass a fake clock + sleep so the 1 s waits take no time.
        self.clock = clock
        self.sleep = sleep
        self.robots: dict[str, RobotFileParser] = {}  # by origin, e.g. "https://smile.com"
        self.last_request_at: dict[str, float] = {}  # by site host
        self.delay: dict[str, float] = {}  # by site host, when robots.txt sets a Crawl-delay

    def crawl(self, website: str) -> list[FetchedPage]:
        """The home page first, then the contact/about/team/services pages it links to."""
        home = self.fetch(start_url(website))
        pages = [home]
        if home.parsed is not None:
            for url in pick_links(home.parsed, self.max_pages - 1):
                pages.append(self.fetch(url))
        return pages

    def fetch(self, url: str) -> FetchedPage:
        """One page, following up to MAX_REDIRECTS redirects by hand (so each hop obeys robots)."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                if not self.allowed(current):
                    return self.logged(FetchedPage(url, current, error="blocked by robots.txt"))
                self.wait_turn(current)
                with self.client.stream("GET", current) as response:
                    if response.has_redirect_location:
                        current = urljoin(current, response.headers["location"])
                        continue
                    return self.logged(read_page(url, current, response))
            except PrivateAddressError:
                return self.logged(FetchedPage(url, current, error="not a public address"))
            except httpx2.TimeoutException:
                return self.logged(FetchedPage(url, current, error="timeout"))
            except httpx2.HTTPError as exc:
                error = f"request failed: {type(exc).__name__}"
                return self.logged(FetchedPage(url, current, error=error))
        return self.logged(FetchedPage(url, current, error=f"more than {MAX_REDIRECTS} redirects"))

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            self.robots[origin] = self.load_robots(origin)
        return self.robots[origin].can_fetch(self.user_agent, url)

    def load_robots(self, origin: str) -> RobotFileParser:
        rules = RobotFileParser()
        try:
            self.wait_turn(origin)
            response = self.client.get(f"{origin}/robots.txt", follow_redirects=True)
        except PrivateAddressError:
            raise  # the page itself is refused too (`fetch()` says why)
        except httpx2.HTTPError:
            rules.disallow_all = True
            return rules

        if response.status_code == 200:
            rules.parse(response.text[:MAX_ROBOTS_CHARS].splitlines())
            crawl_delay = rules.crawl_delay(self.user_agent)
            if crawl_delay:
                self.delay[site_host(origin)] = float(crawl_delay)
        elif 400 <= response.status_code < 500:
            rules.allow_all = True
        else:
            rules.disallow_all = True
        return rules

    def wait_turn(self, url: str) -> None:
        """Sleep until the site's last request is at least 1 s (or its Crawl-delay) ago."""
        host = site_host(url)
        gap = max(MIN_SECONDS_BETWEEN_REQUESTS, self.delay.get(host, 0))
        last = self.last_request_at.get(host)
        if last is not None:
            wait = gap - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self.last_request_at[host] = self.clock()

    def logged(self, page: FetchedPage) -> FetchedPage:
        log.info("page_fetched", url=page.url, status=page.status, error=page.error)
        return page


def read_page(url: str, final_url: str, response: httpx2.Response) -> FetchedPage:
    """Turn a (streamed, not yet read) answer into a FetchedPage. Only 200 HTML is read."""
    page = FetchedPage(url, final_url, status=response.status_code)
    if response.status_code != 200:
        page.error = f"HTTP {response.status_code}"
        return page
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in HTML_TYPES:
        page.error = f"not HTML ({content_type or 'no content type'})"
        return page
    page.parsed = parse_page(read_text(response), final_url)
    return page


def read_text(response: httpx2.Response) -> str:
    """The body as text, at most MAX_PAGE_BYTES of it."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) >= MAX_PAGE_BYTES:
            break
    # `encoding` is the Content-Type charset, or utf-8 when it's missing or unknown.
    return bytes(body[:MAX_PAGE_BYTES]).decode(response.encoding or "utf-8", errors="replace")
