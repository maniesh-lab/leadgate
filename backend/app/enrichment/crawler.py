"""Fetch a few pages of a business's own website, politely.

`Crawler.crawl(website)` fetches the home page, then up to `max_pages - 1` pages
it links to that look like contact / about / team / services pages
(`extract_html.pick_links`). `app.services.enrichment` calls it once per lead.

The rules (PLAN §4, plus 10c's public-addresses-only):
- robots.txt is read once per site and obeyed (RFC 9309: a 4xx answer means no
  rules, a 5xx or no answer means stay out). A `Crawl-delay` there makes us wait longer;
  one over 10 s means stay out.
- at least 1 s between two requests to the same domain (robots.txt included)
- a timeout on every request, and 30 s for a whole answer; at most 3 redirects. A page's
  redirects are followed by hand, so each hop is checked against robots.txt and waits its
  turn; the robots.txt request itself lets httpx2 follow its (max 3) redirects.
- only HTML pages are read (a PDF or an image is skipped), and only the first 2 MB (and
  500 KiB of a robots.txt)
- no logins, no CAPTCHAs, no proxies
- only public internet addresses: a request (or a redirect hop) to localhost, a private
  network (10.x, 192.168.x, the compose services) or a cloud metadata address is refused
  before it's sent (`public_only`), since a lead's website can come from any CSV or n8n.

A page that fails (timeout, 404, not HTML, blocked, a broken address or a page that
breaks the parser) becomes a `FetchedPage` with an `error`; the crawler never raises for
those, so one bad site can't stop a batch.
"""

import contextlib
import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx2
import structlog

from app.enrichment.extract_html import ParsedPage, parse_page, pick_links, site_host
from app.outside_calls import blocked_transport

log = structlog.get_logger(__name__)

TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
# A site whose robots.txt asks for a longer Crawl-delay is left alone.
MAX_CRAWL_DELAY_SECONDS = 10.0
MAX_PAGE_BYTES = 2_000_000
# RFC 9309 asks crawlers to read at least 500 KiB of a robots.txt.
MAX_ROBOTS_BYTES = 512_000
# How long a page (or robots.txt) may take to arrive in full, from the request.
MAX_FETCH_SECONDS = 30.0
# No kept-alive connections: each request opens its own, which `Deadline` can shut. A site
# gets a few pages a second apart, so reusing a connection would save little.
CRAWLER_LIMITS = httpx2.Limits(max_keepalive_connections=0)
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


# IPv6 ranges that carry an IPv4 address in their last 32 bits, which a gateway on the way
# may forward to: NAT64's well-known prefix, IPv4-translated (SIIT) and the old
# IPv4-compatible form. 64:ff9b::a9fe:a9fe would otherwise reach 169.254.169.254.
IPV4_IN_LAST_32_BITS = tuple(
    ipaddress.ip_network(net) for net in ("64:ff9b::/96", "::ffff:0:0:0/96", "::/96")
)


def is_public(address: str) -> bool:
    """False for loopback, private, link-local (cloud metadata), CGNAT, reserved, ...; an
    IPv6 address with an IPv4 one inside (mapped, NAT64, 6to4, Teredo) counts as that one."""
    ip = ipaddress.ip_address(address.split("%")[0])  # drop an IPv6 zone like "%eth0"
    if isinstance(ip, ipaddress.IPv4Address):
        return ip.is_global
    if ip.ipv4_mapped:
        return ip.ipv4_mapped.is_global  # ::ffff:127.0.0.1 is 127.0.0.1
    inside = embedded_ipv4(ip)
    return ip.is_global and (inside is None or inside.is_global)


def embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]  # the client's address
    if int(ip) > 1 and any(ip in net for net in IPV4_IN_LAST_32_BITS):  # not :: or ::1
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


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
    user_agent: str,
    resolve: Callable[[str], list[str]] = resolve_host,
    *,
    blocked: bool = False,
) -> httpx2.Client:
    """The HTTP client for business websites (tests pass a mocked one instead).

    `blocked` (the public demo): every request is refused, before any address lookup.
    """
    return httpx2.Client(
        headers={"User-Agent": user_agent},
        timeout=TIMEOUT_SECONDS,
        limits=CRAWLER_LIMITS,
        # Only robots.txt requests follow redirects by themselves; pages do it in `fetch()`.
        max_redirects=MAX_REDIRECTS,
        event_hooks={"request": [] if blocked else [public_only(resolve)]},
        transport=blocked_transport() if blocked else None,
    )


class Deadline:
    """Shuts the request's connection down if it isn't over `seconds` from now. httpx's
    timeout is per read, so a server sending its headers (or page) a byte every few seconds
    could hold the worker for days. Give the request `extensions=deadline.extensions`: it
    hears of each connection the request opens (a new one each time: `CRAWLER_LIMITS`)."""

    def __init__(self, seconds: float) -> None:
        self.passed = False
        self.extensions = {"trace": self._trace}
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    def __enter__(self) -> Deadline:
        self._timer.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._timer.cancel()

    def _trace(self, event: str, info: dict[str, Any]) -> None:
        if event in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            sock = info["return_value"].get_extra_info("socket")
            with self._lock:
                self._sockets.append(sock)
                if self.passed:
                    shut(sock)

    def _expire(self) -> None:
        with self._lock:
            self.passed = True
            for sock in self._sockets:
                shut(sock)


def shut(sock: socket.socket | None) -> None:
    """Wake a read blocked on `sock` with end-of-stream. The plain socket call, even for a
    TLS socket: its own `shutdown()` would pull the TLS state from under the reading thread."""
    if sock is None:
        return
    with contextlib.suppress(OSError):  # already closed
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


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
            watchdog = Deadline(MAX_FETCH_SECONDS)  # headers included (see `read_text()`)
            try:
                if not self.allowed(current):
                    return self.logged(FetchedPage(url, current, error="blocked by robots.txt"))
                self.wait_turn(current)
                deadline = self.clock() + MAX_FETCH_SECONDS
                with (
                    watchdog,
                    self.client.stream("GET", current, extensions=watchdog.extensions) as response,
                ):
                    if response.has_redirect_location:
                        current = urljoin(current, response.headers["location"])
                        continue
                    return self.logged(read_page(url, current, response, deadline, self.clock))
            except PrivateAddressError:
                return self.logged(FetchedPage(url, current, error="not a public address"))
            except httpx2.InvalidURL, ValueError:  # e.g. "http://x.test:abc/" from a CSV
                return self.logged(FetchedPage(url, current, error="not a valid address"))
            except httpx2.TimeoutException:
                return self.logged(FetchedPage(url, current, error="timeout"))
            except httpx2.HTTPError as exc:
                error = "timeout" if watchdog.passed else f"request failed: {type(exc).__name__}"
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
            deadline = self.clock() + MAX_FETCH_SECONDS
            url = f"{origin}/robots.txt"
            # Streamed like a page, so a huge (or gzip-bombed) robots.txt is cut, not
            # loaded whole into memory; the watchdog covers the headers too.
            watchdog = Deadline(MAX_FETCH_SECONDS)
            with (
                watchdog,
                self.client.stream(
                    "GET", url, follow_redirects=True, extensions=watchdog.extensions
                ) as response,
            ):
                status = response.status_code
                text = (
                    read_text(response, MAX_ROBOTS_BYTES, deadline, self.clock)
                    if status == 200
                    else ""
                )
        except PrivateAddressError:
            raise  # the page itself is refused too (`fetch()` says why)
        except httpx2.HTTPError:
            rules.disallow_all = True
            return rules

        if status == 200:
            rules.parse(text.splitlines())
            try:
                crawl_delay = float(rules.crawl_delay(self.user_agent) or 0)
            except OverflowError:  # a 400-digit Crawl-delay: too long to wait, like any other
                crawl_delay = float("inf")
            if crawl_delay > MAX_CRAWL_DELAY_SECONDS:
                # Coming faster than asked would be rude, and waiting that long would hold
                # the worker (one job at a time) up for every other lead: stay out.
                rules.disallow_all = True
            elif crawl_delay:
                self.delay[site_host(origin)] = crawl_delay
        elif 400 <= status < 500:
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


def read_page(
    url: str,
    final_url: str,
    response: httpx2.Response,
    deadline: float,
    clock: Callable[[], float],
) -> FetchedPage:
    """Turn a (streamed, not yet read) answer into a FetchedPage. Only 200 HTML is read."""
    page = FetchedPage(url, final_url, status=response.status_code)
    if response.status_code != 200:
        page.error = f"HTTP {response.status_code}"
        return page
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in HTML_TYPES:
        page.error = f"not HTML ({content_type or 'no content type'})"
        return page
    text = read_text(response, MAX_PAGE_BYTES, deadline, clock)
    try:
        page.parsed = parse_page(text, final_url)
    except RecursionError, ValueError:  # a page built to break the parser
        page.error = "couldn't read the page"
    return page


def read_text(
    response: httpx2.Response, max_bytes: int, deadline: float, clock: Callable[[], float]
) -> str:
    """The body as text: at most `max_bytes` of it, all there by `deadline` (the timeout
    is per read, so a server sending a byte every few seconds could take days)."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) >= max_bytes:
            break
        if clock() > deadline:
            raise httpx2.ReadTimeout("the answer took too long", request=response.request)
    return decode(bytes(body[:max_bytes]), response.encoding)


def decode(body: bytes, encoding: str | None) -> str:
    """`encoding` is the Content-Type charset, or utf-8 when it's missing or unknown. A
    codec Python has that doesn't make text (like `rot13`) counts as unknown."""
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
