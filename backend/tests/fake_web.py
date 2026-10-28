"""A fake internet for the crawler and enrichment tests: no request ever leaves the machine.

`FakeWeb` answers every request from a dict of URL -> (kind, value); `FakeClock`
stands in for time, so the crawler's 1 s waits take no time. `make_crawler()` wires
both into a real `Crawler`. `SMILE_SITE` is a small dentist website built from the
HTML in tests/fixtures/websites/ (its `.test` domain is reserved, never a real site).
"""

from pathlib import Path

import httpx2

from app.enrichment.crawler import Crawler

FIXTURES = Path(__file__).parent / "fixtures" / "websites"
USER_AGENT = "leadgate/0.1 (+https://github.com/maniesh-lab/leadgate)"

SMILE = "https://www.smileaustin.test"
SMILE_SITE = {
    "http://www.smileaustin.test/robots.txt": ("text", (FIXTURES / "smile_robots.txt").read_text()),
    "http://www.smileaustin.test/": ("redirect", SMILE + "/"),
    f"{SMILE}/robots.txt": ("text", (FIXTURES / "smile_robots.txt").read_text()),
    f"{SMILE}/": ("html", (FIXTURES / "smile_home.html").read_text()),
    f"{SMILE}/contact-us": ("html", (FIXTURES / "smile_contact.html").read_text()),
    f"{SMILE}/about": ("html", (FIXTURES / "smile_about.html").read_text()),
    f"{SMILE}/meet-the-team/": ("html", "<p>private</p>"),
}


class FakeWeb:
    """Unknown URLs answer 404. Remembers every request URL in `requests`.

    Kinds: "html", "text" (200 with that body), "redirect" (301 to value), "pdf",
    "timeout", "down" (connection refused), "status" (an empty answer with that code).
    """

    def __init__(self, routes: dict[str, tuple[str, str]]) -> None:
        self.routes = routes
        self.requests: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = f"{request.url.scheme}://{request.url.host}{request.url.raw_path.decode()}"
        self.requests.append(url)
        kind, value = self.routes.get(url, ("status", "404"))
        if kind == "html":
            html_type = {"content-type": "text/html; charset=utf-8"}
            return httpx2.Response(200, headers=html_type, text=value)
        if kind == "text":
            return httpx2.Response(200, headers={"content-type": "text/plain"}, text=value)
        if kind == "redirect":
            return httpx2.Response(301, headers={"location": value})
        if kind == "pdf":
            pdf_type = {"content-type": "application/pdf"}
            return httpx2.Response(200, headers=pdf_type, content=b"%PDF")
        if kind == "timeout":
            raise httpx2.ReadTimeout("timed out", request=request)
        if kind == "down":
            raise httpx2.ConnectError("connection refused", request=request)
        return httpx2.Response(int(value))


class FakeClock:
    """Time that only moves when someone sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_crawler(web: FakeWeb, clock: FakeClock | None = None, max_pages: int = 4) -> Crawler:
    clock = clock or FakeClock()
    client = httpx2.Client(transport=httpx2.MockTransport(web))
    return Crawler(client, USER_AGENT, max_pages=max_pages, clock=clock, sleep=clock.sleep)
