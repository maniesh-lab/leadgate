"""Tests for the website signals (`app.enrichment.signals`)."""

from pathlib import Path

import pytest

from app.enrichment.extract_html import parse_page
from app.enrichment.signals import latest_copyright_year, no_site_signals, site_signals

FIXTURES = Path(__file__).parents[1] / "fixtures" / "websites"


def test_modern_site() -> None:
    home = parse_page((FIXTURES / "smile_home.html").read_text(), "https://www.smileaustin.test/")
    contact = parse_page(
        (FIXTURES / "smile_contact.html").read_text(), "https://www.smileaustin.test/contact-us"
    )

    assert site_signals(home, [home, contact], current_year=2026) == {
        "page_load_ok": True,
        "https": True,
        "mobile_viewport": True,
        "copyright_year": 2019,
        "has_form": True,  # the form is on the contact page
        "https_and_mobile": True,
    }


def test_old_site() -> None:
    home = parse_page((FIXTURES / "old_site.html").read_text(), "http://oldtowndental.test/")

    assert site_signals(home, [home], current_year=2026) == {
        "page_load_ok": True,
        "https": False,
        "mobile_viewport": False,
        "copyright_year": 2012,
        "has_form": False,
        "https_and_mobile": False,
    }


def test_https_without_a_mobile_viewport_is_not_both() -> None:
    home = parse_page("<p>hi</p>", "https://plain.test/")
    assert site_signals(home, [home], current_year=2026)["https_and_mobile"] is False


def test_a_site_that_did_not_load_is_unknown_not_bad() -> None:
    signals = site_signals(None, [], current_year=2026)
    assert signals["page_load_ok"] is False
    assert all(signals[name] is None for name in signals if name != "page_load_ok")


def test_no_website_means_nothing_to_load() -> None:
    signals = no_site_signals()
    assert signals["page_load_ok"] is None
    assert set(signals) == set(site_signals(None, [], current_year=2026))


@pytest.mark.parametrize(
    ("text", "year"),
    [
        ("© 2019 Smile", 2019),
        ("Copyright © 2015 - 2024 Smile", 2024),
        ("copyright 2010–2013", 2013),
        ("(c) 2008 Old Town", 2008),
        ("COPYRIGHT2017", 2017),
        ("Founded 1998, renovated 2015", None),  # years, but no copyright notice
        ("© 2031 Typo Dental", None),  # in the future
        ("© 1975 Very Old", None),  # before the web
    ],
)
def test_copyright_year(text: str, year: int | None) -> None:
    assert latest_copyright_year([text], current_year=2026) == year


def test_copyright_year_is_the_newest_over_all_pages() -> None:
    assert latest_copyright_year(["© 2012", "© 2019", "no notice"], current_year=2026) == 2019
