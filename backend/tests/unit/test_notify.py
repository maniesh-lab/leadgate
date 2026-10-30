"""Tests for the notifiers. No network: Discord is answered by `httpx2.MockTransport`."""

import json

import httpx2
import pytest

from app.config import Settings
from app.services.notify import (
    MAX_LENGTH,
    MAX_WAIT_SECONDS,
    Channel,
    DiscordNotifier,
    FakeNotifier,
    NotifyError,
    make_notifier,
)

LEADS_URL = "https://discord.test/api/webhooks/1/leads-token"
ADMIN_URL = "https://discord.test/api/webhooks/2/admin-token"


class FakeDiscord:
    """Answers 204 like Discord (or the queued `answers` first) and remembers each request."""

    def __init__(self, answers: list[httpx2.Response] | None = None) -> None:
        self.answers = list(answers or [])
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else httpx2.Response(204)


def make_discord(fake: FakeDiscord, waits: list[float] | None = None) -> DiscordNotifier:
    client = httpx2.Client(transport=httpx2.MockTransport(fake))
    sleep = waits.append if waits is not None else (lambda _seconds: None)
    urls = {Channel.LEADS: LEADS_URL, Channel.ADMIN: ADMIN_URL}
    return DiscordNotifier(client, urls, sleep=sleep)


def test_each_channel_posts_to_its_own_webhook() -> None:
    fake = FakeDiscord()
    discord = make_discord(fake)

    discord.send(Channel.LEADS, "New approved lead")
    discord.send(Channel.ADMIN, "Needs a second look")

    assert [str(request.url) for request in fake.requests] == [LEADS_URL, ADMIN_URL]
    body = json.loads(fake.requests[0].content)
    assert body == {"content": "New approved lead", "allowed_mentions": {"parse": []}}


def test_long_messages_are_cut_to_discords_limit() -> None:
    fake = FakeDiscord()
    make_discord(fake).send(Channel.LEADS, "x" * 3000)
    assert len(json.loads(fake.requests[0].content)["content"]) == MAX_LENGTH


def test_a_rate_limit_waits_as_long_as_discord_asks() -> None:
    fake = FakeDiscord([httpx2.Response(429, json={"retry_after": 1.5, "global": False})])
    waits: list[float] = []

    make_discord(fake, waits).send(Channel.LEADS, "hi")

    assert waits == [1.5]
    assert len(fake.requests) == 2


def test_a_rate_limit_wait_is_capped() -> None:
    fake = FakeDiscord([httpx2.Response(429, headers={"Retry-After": "600"})])
    waits: list[float] = []
    make_discord(fake, waits).send(Channel.LEADS, "hi")
    assert waits == [MAX_WAIT_SECONDS]


def test_an_error_never_shows_the_webhook_url() -> None:
    fake = FakeDiscord([httpx2.Response(404, json={"message": "Unknown Webhook", "code": 10015})])
    with pytest.raises(NotifyError, match="404.*Unknown Webhook") as caught:
        make_discord(fake).send(Channel.LEADS, "hi")
    assert "leads-token" not in str(caught.value)


def test_a_network_error_never_shows_the_webhook_url() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    discord = DiscordNotifier(
        httpx2.Client(transport=httpx2.MockTransport(fail)), {Channel.LEADS: LEADS_URL}
    )
    with pytest.raises(NotifyError) as caught:
        discord.send(Channel.LEADS, "hi")
    assert str(caught.value) == "Discord request failed: ConnectError"


def test_make_notifier(settings: Settings) -> None:
    client = httpx2.Client()
    assert isinstance(make_notifier(settings, client), FakeNotifier)

    one = make_notifier(settings.model_copy(update={"discord_webhook_url": LEADS_URL}), client)
    assert isinstance(one, DiscordNotifier)
    # Without an admin webhook, escalations go to the leads channel.
    assert one.webhook_urls == {Channel.LEADS: LEADS_URL, Channel.ADMIN: LEADS_URL}

    both = settings.model_copy(
        update={"discord_webhook_url": LEADS_URL, "discord_admin_webhook_url": ADMIN_URL}
    )
    assert make_notifier(both, client).webhook_urls[Channel.ADMIN] == ADMIN_URL
