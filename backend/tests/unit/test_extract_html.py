"""Tests for reading one HTML page (`app.enrichment.extract_html`).

The pages in tests/fixtures/websites/ are hand-written but realistic: a modern
dentist site (Smile Austin) and a 2000s-style one (Old Town). Their `.test`
domains are reserved, so they can never be real sites.
"""

from pathlib import Path

import pytest

from app.enrichment.extract_html import (
    Link,
    ParsedPage,
    find_emails,
    find_phones,
    find_socials,
    mailto_emails,
    parse_page,
    pick_links,
    same_page,
    unique_phones,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "websites"
SMILE = "https://www.smileaustin.test/"


def parse_fixture(name: str, url: str = SMILE) -> ParsedPage:
    return parse_page((FIXTURES / name).read_text(), url)


def test_visible_text_skips_scripts_styles_and_head() -> None:
    page = parse_fixture("smile_home.html")

    assert "Gentle family dentistry in South Austin" in page.text
    assert "© 2016 - 2019 Smile Austin Dental" in page.text
    assert "sentry" not in page.text  # <script>
    assert "background" not in page.text  # <style>
    assert "Family & Cosmetic Dentist" not in page.text  # <title> is in <head>
    assert "turn on JavaScript" not in page.text  # <noscript>
    assert "  " not in page.text and "\n" not in page.text


def test_emails_from_mailto_and_text() -> None:
    home = parse_fixture("smile_home.html")
    contact = parse_fixture("smile_contact.html", SMILE + "contact-us")

    # The mailto: subject is dropped, case is folded, and the logo@2x.png file name is ignored.
    assert home.emails == ["info@smileaustin.test"]
    assert contact.emails == ["frontdesk@smileaustin.test", "drsmith.smile@gmail.com"]


def test_mailto_with_several_addresses_and_encoding() -> None:
    assert mailto_emails(["mailto:a@shop.test,%20B@shop.test?cc=c@shop.test", "mailto:"]) == [
        "a@shop.test",
        "b@shop.test",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "logo@2x.png",
        "icon@3x.webp",
        "you@example.com",
        "abc@o1.ingest.sentry.io",
        "x@sentry-next.wixpress.com",
        "not an email at all",
    ],
)
def test_junk_is_not_an_email(text: str) -> None:
    assert find_emails(text) == []


def test_a_very_long_word_does_not_hang_the_email_search() -> None:
    # Before the regex had length limits, this took minutes.
    assert find_emails("a" * 200_000 + " write to info@smile.test") == ["info@smile.test"]


def test_phones_from_tel_links_and_text() -> None:
    home = parse_fixture("smile_home.html")
    contact = parse_fixture("smile_contact.html", SMILE + "contact-us")
    old = parse_fixture("old_site.html", "http://oldtowndental.test/")

    # The tel: link and the text show the same number: kept once, the tel: spelling first.
    assert home.phones == ["+1-512-555-0142"]
    assert contact.phones == ["(512) 555-0142", "512.555.0143"]
    assert old.phones == ["512-555-0199"]


@pytest.mark.parametrize(
    ("text", "phones"),
    [
        ("Call +977 1-4412345 today", ["+977 1-4412345"]),
        ("Kathmandu office 01-4412345", ["01-4412345"]),
        ("London 020 7946 0958", ["020 7946 0958"]),
        ("Two lines: 512-555-0101 512-555-0102", ["512-555-0101", "512-555-0102"]),
        ("Open 2009-2012, hours 9-5, zip 78745", []),
    ],
)
def test_phone_shapes(text: str, phones: list[str]) -> None:
    assert find_phones(text) == phones


def test_unique_phones_treats_a_country_code_as_the_same_number() -> None:
    assert unique_phones(["(512) 555-0142", "+1 512 555 0142", "512-555-0143", "12345"]) == [
        "(512) 555-0142",
        "512-555-0143",
    ]


def test_socials_skip_share_buttons() -> None:
    home = parse_fixture("smile_home.html")
    about = parse_fixture("smile_about.html", SMILE + "about")

    assert find_socials(home.links) == {
        "facebook": "https://www.facebook.com/smileaustindental",
        "instagram": "https://instagram.com/smileaustin/",
    }
    assert find_socials(about.links) == {
        "linkedin": "https://www.linkedin.com/company/smile-austin-dental"
    }


def test_socials_x_and_twitter_are_one_network() -> None:
    links = [Link("https://x.com/smile", ""), Link("https://twitter.com/smile_old", "")]
    assert find_socials(links) == {"x": "https://x.com/smile"}


def test_socials_ignore_the_bare_network_home_page() -> None:
    assert find_socials([Link("https://www.facebook.com/", "Facebook")]) == {}


def test_viewport_and_form() -> None:
    assert parse_fixture("smile_home.html").has_mobile_viewport is True
    assert parse_fixture("old_site.html").has_mobile_viewport is False
    # A contact form with a textarea counts; the home page has no form.
    assert parse_fixture("smile_contact.html").has_form is True
    assert parse_fixture("smile_home.html").has_form is False


def test_a_search_box_alone_is_not_a_contact_form() -> None:
    page = parse_page('<form action="/s"><input type="search" name="q"></form>', SMILE)
    assert page.has_form is False


def test_a_booking_form_with_a_phone_field_counts() -> None:
    page = parse_page('<form><input type="tel" name="phone"><button>Book</button></form>', SMILE)
    assert page.has_form is True


def test_a_viewport_without_device_width_is_not_mobile() -> None:
    page = parse_page('<head><meta name="viewport" content="width=1024"></head>', SMILE)
    assert page.has_mobile_viewport is False


def test_pick_links_one_per_keyword_on_the_same_site() -> None:
    home = parse_fixture("smile_home.html")

    # contact, about, team, service order; the #map fragment is dropped; the blog, the
    # PDF, the outside link and the home page itself are never picked.
    assert pick_links(home, 3) == [
        SMILE + "contact-us",
        SMILE + "about",
        SMILE + "meet-the-team/",
    ]
    assert pick_links(home, 4)[-1] == SMILE + "services/"
    assert pick_links(home, 1) == [SMILE + "contact-us"]
    assert pick_links(home, 0) == []


def test_pick_links_treats_www_and_no_www_as_one_site() -> None:
    page = parse_page('<a href="https://smileaustin.test/contact">Reach us</a>', SMILE)
    assert pick_links(page, 3) == ["https://smileaustin.test/contact"]


def test_pick_links_matches_the_link_text_too() -> None:
    page = parse_page('<a href="/page?id=7">About the doctor</a>', SMILE)
    assert pick_links(page, 3) == [SMILE + "page?id=7"]


def test_same_page_ignores_scheme_www_and_trailing_slash() -> None:
    assert same_page("http://smileaustin.test", "https://www.smileaustin.test/")
    assert not same_page(SMILE + "about", SMILE)
