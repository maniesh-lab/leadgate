"""Phone cleaning: find real phone numbers in any text and write them one standard way.

Sources and websites write phones every way there is ("(512) 707-7300",
"+1-512-707-7300", "512.707.7300; 512.646.4505"). The `phonenumbers` library (a
port of Google's libphonenumber) finds the numbers in the text, drops ones that
can't be real (a ZIP+4 like "78745-1234"), and formats the rest as E.164
("+15127077300"). One format means the `check` node can spot the same phone on two
leads with a plain equality test.

A number without a country code ("512-707-7300") needs the campaign's country to be
understood; without one, only numbers written with "+" are found.

Called by the `extract` node (via `app.agents.cleaning.lead`).
"""

import phonenumbers
from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberMatcher


def find_phones(text: str | None, country: str | None) -> list[str]:
    """Every valid phone number in `text`, as E.164, in the order found, without repeats.

    `country` is a two-letter code ("US", "np"), used for numbers without a "+".
    """
    if not text:
        return []
    region = country.upper() if country else None
    found: list[str] = []
    for match in PhoneNumberMatcher(text, region, leniency=Leniency.VALID):
        number = phonenumbers.format_number(match.number, PhoneNumberFormat.E164)
        if number not in found:
            found.append(number)
    return found
