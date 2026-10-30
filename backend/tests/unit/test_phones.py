"""Tests for phone cleaning (`app.agents.cleaning.phones`)."""

from app.agents.cleaning.phones import find_phones


def test_every_way_of_writing_a_us_number_becomes_e164() -> None:
    for text in ["(512) 707-7300", "+1-512-707-7300", "512.707.7300", "5127077300"]:
        assert find_phones(text, "US") == ["+15127077300"], text


def test_finds_several_numbers_and_drops_repeats() -> None:
    text = "Call (512) 707-7300 or 512.646.4505. Again: +1 512 707 7300"
    assert find_phones(text, "us") == ["+15127077300", "+15126464505"]


def test_a_zip_plus_four_is_not_a_phone() -> None:
    assert find_phones("Austin, TX 78745-1234", "US") == []


def test_without_a_country_only_plus_numbers_are_found() -> None:
    assert find_phones("512-707-7300", None) == []
    assert find_phones("+977 1-4412345", None) == ["+97714412345"]


def test_empty_text() -> None:
    assert find_phones(None, "US") == []
    assert find_phones("", "US") == []
