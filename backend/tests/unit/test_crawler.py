"""Tests for the polite website crawler (`app.enrichment.crawler`).

No network: `tests.fake_web` answers every request from a dict of URL -> answer, and a
`FakeClock` stands in for time, so the 1 s waits between requests take no time.
"""

import httpx2
import pytest

from app.enrichment.crawler import (
    MAX_PAGE_BYTES,
    Crawler,
    PrivateAddressError,
    is_public,
    make_crawler_client,
    public_only,
    start_url,
)
from tests.fake_web import (
    FIXTURES,
    SMILE,
    SMILE_SITE,
    USER_AGENT,
    FakeClock,
    FakeWeb,
    make_crawler,
)


def test_crawls_the_home_page_and_its_contact_and_about_pages() -> None:
    web = FakeWeb(SMILE_SITE)
    clock = FakeClock()

    pages = make_crawler(web, clock).crawl("www.smileaustin.test")

    assert [page.url for page in pages] == [
        "http://www.smileaustin.test",
        f"{SMILE}/contact-us",
        f"{SMILE}/about",
        f"{SMILE}/meet-the-team/",
    ]
    home, contact, about, team = pages
    # http -> https redirect followed; robots.txt read for both origins.
    assert (home.final_url, home.status, home.error) == (f"{SMILE}/", 200, None)
    assert home.parsed is not None and home.parsed.url == f"{SMILE}/"
    assert contact.parsed is not None and contact.parsed.has_form
    assert about.parsed is not None
    # robots.txt disallows the team page, so it was never requested.
    assert (team.parsed, team.error) == (None, "blocked by robots.txt")
    assert f"{SMILE}/meet-the-team/" not in web.requests
    assert web.requests == [
        "http://www.smileaustin.test/robots.txt",
        "http://www.smileaustin.test/",
        f"{SMILE}/robots.txt",
        f"{SMILE}/",
        f"{SMILE}/contact-us",
        f"{SMILE}/about",
    ]
    # One request per second to the same site: a 1 s wait before every request but the first.
    assert clock.sleeps == [1.0] * (len(web.requests) - 1)


def test_summary_has_no_html() -> None:
    home = make_crawler(FakeWeb(SMILE_SITE)).crawl(SMILE)[0]
    assert home.summary() == {
        "url": "https://www.smileaustin.test",
        "final_url": SMILE,
        "status": 200,
        "error": None,
    }


def test_max_pages_limits_the_crawl() -> None:
    web = FakeWeb(SMILE_SITE)
    pages = make_crawler(web, max_pages=2).crawl(SMILE)
    assert [page.url for page in pages] == [SMILE, f"{SMILE}/contact-us"]


def test_a_site_whose_robots_txt_disallows_everything_is_never_fetched() -> None:
    web = FakeWeb(
        {
            "https://blocked.test/robots.txt": (
                "text",
                (FIXTURES / "blocked_robots.txt").read_text(),
            ),
            "https://blocked.test/": ("html", "<p>secret</p>"),
        }
    )
    pages = make_crawler(web).crawl("https://blocked.test/")

    assert len(pages) == 1
    assert (pages[0].parsed, pages[0].error) == (None, "blocked by robots.txt")
    assert web.requests == ["https://blocked.test/robots.txt"]


def test_robots_rules_for_our_user_agent_apply_to_us() -> None:
    robots = "User-agent: leadgate\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    web = FakeWeb({"https://a.test/robots.txt": ("text", robots), "https://a.test/": ("html", "x")})
    assert make_crawler(web).crawl("https://a.test/")[0].error == "blocked by robots.txt"


def test_robots_rules_for_other_bots_do_not_apply_to_us() -> None:
    robots = "User-agent: otherbot\nDisallow: /\n"
    web = FakeWeb({"https://a.test/robots.txt": ("text", robots), "https://a.test/": ("html", "x")})
    assert make_crawler(web).crawl("https://a.test/")[0].parsed is not None


def test_no_robots_txt_means_no_rules() -> None:
    web = FakeWeb({"https://a.test/": ("html", "<p>hello</p>")})  # robots.txt -> 404
    page = make_crawler(web).crawl("https://a.test/")[0]
    assert page.parsed is not None and page.parsed.text == "hello"


def test_robots_txt_server_error_means_stay_out() -> None:
    web = FakeWeb(
        {"https://a.test/robots.txt": ("status", "503"), "https://a.test/": ("html", "x")}
    )
    assert make_crawler(web).crawl("https://a.test/")[0].error == "blocked by robots.txt"
    assert web.requests == ["https://a.test/robots.txt"]


def test_robots_txt_unreachable_means_stay_out() -> None:
    web = FakeWeb({"https://a.test/robots.txt": ("down", ""), "https://a.test/": ("html", "x")})
    assert make_crawler(web).crawl("https://a.test/")[0].error == "blocked by robots.txt"


def test_robots_txt_is_read_once_per_site() -> None:
    web = FakeWeb(SMILE_SITE)
    crawler = make_crawler(web)
    crawler.crawl(SMILE)
    crawler.crawl(SMILE)
    assert web.requests.count(f"{SMILE}/robots.txt") == 1


def test_crawl_delay_in_robots_txt_slows_us_down() -> None:
    robots = "User-agent: *\nCrawl-delay: 5\n"
    web = FakeWeb(
        {
            "https://slow.test/robots.txt": ("text", robots),
            "https://slow.test/": ("html", '<a href="/contact">Contact</a>'),
            "https://slow.test/contact": ("html", "<p>hi</p>"),
        }
    )
    clock = FakeClock()
    make_crawler(web, clock).crawl("https://slow.test/")
    assert clock.sleeps == [5.0, 5.0]


def test_waits_are_per_site() -> None:
    web = FakeWeb({"https://a.test/": ("html", "a"), "https://b.test/": ("html", "b")})
    clock = FakeClock()
    crawler = make_crawler(web, clock)
    crawler.crawl("https://a.test/")
    crawler.crawl("https://b.test/")
    # robots.txt then the page on each site: one wait per site, none between the sites.
    assert clock.sleeps == [1.0, 1.0]


def test_a_relative_redirect_is_followed() -> None:
    web = FakeWeb({"https://a.test/": ("redirect", "/home"), "https://a.test/home": ("html", "hi")})
    page = make_crawler(web).fetch("https://a.test/")
    assert (page.final_url, page.status) == ("https://a.test/home", 200)


def test_more_than_three_redirects_is_an_error() -> None:
    web = FakeWeb({f"https://a.test/{n}": ("redirect", f"/{n + 1}") for n in range(10)})
    page = make_crawler(web).fetch("https://a.test/0")
    assert (page.parsed, page.error) == (None, "more than 3 redirects")
    assert page.final_url == "https://a.test/4"


def test_a_redirect_into_a_blocked_area_is_not_followed() -> None:
    robots = "User-agent: *\nDisallow: /private\n"
    web = FakeWeb(
        {
            "https://a.test/robots.txt": ("text", robots),
            "https://a.test/": ("redirect", "/private/home"),
            "https://a.test/private/home": ("html", "secret"),
        }
    )
    page = make_crawler(web).fetch("https://a.test/")
    assert page.error == "blocked by robots.txt"
    assert "https://a.test/private/home" not in web.requests


def test_a_404_home_page_stops_the_crawl() -> None:
    web = FakeWeb({})
    pages = make_crawler(web).crawl("https://gone.test/")
    assert len(pages) == 1
    assert (pages[0].status, pages[0].error, pages[0].parsed) == (404, "HTTP 404", None)


def test_a_non_html_answer_is_skipped() -> None:
    web = FakeWeb({"https://a.test/": ("pdf", "")})
    page = make_crawler(web).fetch("https://a.test/")
    assert (page.status, page.error, page.parsed) == (200, "not HTML (application/pdf)", None)


def test_a_slow_site_times_out() -> None:
    web = FakeWeb({"https://slow.test/": ("timeout", "")})
    page = make_crawler(web).fetch("https://slow.test/")
    assert (page.status, page.error, page.parsed) == (None, "timeout", None)


def test_a_site_that_is_down() -> None:
    web = FakeWeb({"https://a.test/": ("down", "")})
    page = make_crawler(web).fetch("https://a.test/")
    assert page.error == "request failed: ConnectError"


def test_a_huge_page_is_cut_at_the_size_cap() -> None:
    web = FakeWeb({"https://big.test/": ("html", "<p>" + "a" * (MAX_PAGE_BYTES + 5000) + "</p>")})
    page = make_crawler(web).fetch("https://big.test/")
    assert page.parsed is not None
    assert len(page.parsed.text) <= MAX_PAGE_BYTES


def test_start_url_adds_a_scheme_when_missing() -> None:
    assert start_url(" www.smile.test ") == "http://www.smile.test"
    assert start_url("https://smile.test/") == "https://smile.test/"


# --- Only public addresses (SSRF guard) ---


def guarded_crawler(web: FakeWeb, dns: dict[str, list[str]]) -> Crawler:
    """A crawler whose client has the real `public_only` hook, with `dns` as the resolver."""
    client = httpx2.Client(
        transport=httpx2.MockTransport(web),
        event_hooks={"request": [public_only(lambda host: dns.get(host, []))]},
    )
    clock = FakeClock()
    return Crawler(client, USER_AGENT, clock=clock, sleep=clock.sleep)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.18.0.3", False),  # a compose service
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
    ],
)
def test_is_public(address: str, public: bool) -> None:
    assert is_public(address) is public


def test_refuses_a_site_on_a_private_address_without_sending_anything() -> None:
    web = FakeWeb({"http://intranet.test/": ("html", "<p>secret</p>")})
    crawler = guarded_crawler(web, {"intranet.test": ["10.0.0.5"]})

    pages = crawler.crawl("intranet.test")

    assert [(page.url, page.error) for page in pages] == [
        ("http://intranet.test", "not a public address")
    ]
    assert web.requests == []


def test_refuses_an_ip_literal_like_the_api_itself() -> None:
    web = FakeWeb({})
    pages = guarded_crawler(web, {}).crawl("http://127.0.0.1:8000/api/ready")

    assert pages[0].error == "not a public address"
    assert web.requests == []


def test_refuses_a_redirect_to_the_cloud_metadata_address() -> None:
    web = FakeWeb(
        {
            "http://evil.test/robots.txt": ("status", "404"),
            "http://evil.test/": ("redirect", "http://169.254.169.254/latest/meta-data/"),
        }
    )
    crawler = guarded_crawler(web, {"evil.test": ["93.184.215.14"]})

    pages = crawler.crawl("evil.test")

    assert pages[0].error == "not a public address"
    assert pages[0].final_url == "http://169.254.169.254/latest/meta-data/"
    assert not any("169.254" in url for url in web.requests)


def test_a_public_site_is_crawled_as_usual() -> None:
    web = FakeWeb(SMILE_SITE)
    dns = {"www.smileaustin.test": ["93.184.215.14"]}

    pages = guarded_crawler(web, dns).crawl("www.smileaustin.test")

    assert pages[0].error is None and pages[0].parsed is not None


def test_the_real_client_has_the_guard() -> None:
    # The hook refuses before anything is sent, so this makes no network request.
    client = make_crawler_client(USER_AGENT, resolve=lambda _host: ["192.168.1.20"])
    with client, pytest.raises(PrivateAddressError):
        client.get("http://printer.test/")
