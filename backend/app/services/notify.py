"""Messages to people: the `Notifier` interface, `DiscordNotifier`, and `FakeNotifier`.

Two channels: `leads` (a new approved lead) and `admin` (a lead that needs a second
look). The `act` node sends at most one message per run item: it records `notified`
in the graph state and skips the send when it's already true.

`make_notifier(settings, client)` picks the implementation: `DiscordNotifier` when
DISCORD_WEBHOOK_URL is set, else `FakeNotifier` (tests, CI, demos without Discord).
Called by the worker (`app.worker.main.open_worker`).
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum

import httpx2
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

# Discord's limit for a message's `content`.
MAX_LENGTH = 2000
# A 429 tells how long to wait (`retry_after`, seconds); never wait longer than this.
MAX_WAIT_SECONDS = 60
TRIES = 2


class Channel(StrEnum):
    LEADS = "leads"
    ADMIN = "admin"


class NotifyError(Exception):
    """The message wasn't posted. The `act` node fails, and the worker retries the job."""


class Notifier(ABC):
    @abstractmethod
    def send(self, channel: Channel, text: str) -> None:
        """Post one message to the channel."""


class FakeNotifier(Notifier):
    """Remembers every message in `sent` (and logs it) instead of posting it."""

    def __init__(self) -> None:
        self.sent: list[tuple[Channel, str]] = []

    def send(self, channel: Channel, text: str) -> None:
        self.sent.append((channel, text))
        log.info("fake_message", channel=channel.value, text=text)


class DiscordNotifier(Notifier):
    """Posts to a Discord webhook per channel (a webhook belongs to one Discord channel).

    The webhook URL holds its secret token, so it never goes into errors or logs.
    """

    def __init__(
        self,
        client: httpx2.Client,
        webhook_urls: dict[Channel, str],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.webhook_urls = webhook_urls
        self.sleep = sleep  # tests pass a fake, so they don't wait

    def send(self, channel: Channel, text: str) -> None:
        body = {
            "content": text[:MAX_LENGTH],
            # Never ping anyone, even if a business name has "@everyone" in it.
            "allowed_mentions": {"parse": []},
        }
        for attempt in range(1, TRIES + 1):
            try:
                response = self.client.post(self.webhook_urls[channel], json=body)
            except httpx2.HTTPError as exc:
                raise NotifyError(f"Discord request failed: {type(exc).__name__}") from exc
            if response.status_code == 429 and attempt < TRIES:
                wait = retry_after(response)
                log.warning("discord_rate_limited", channel=channel.value, wait_seconds=wait)
                self.sleep(wait)
                continue
            if response.is_error:
                raise NotifyError(f"Discord answered {response.status_code}: {response.text[:300]}")
            log.info("message_sent", channel=channel.value)
            return


def retry_after(response: httpx2.Response) -> float:
    """Seconds Discord asks us to wait: the body's `retry_after`, else the header, else 1."""
    try:
        seconds = float(response.json()["retry_after"])
    except ValueError, KeyError, TypeError:
        seconds = float(response.headers.get("Retry-After", 1))
    return min(max(seconds, 0), MAX_WAIT_SECONDS)


def make_notifier(settings: Settings, client: httpx2.Client) -> Notifier:
    """`DiscordNotifier` when DISCORD_WEBHOOK_URL is set (admin falls back to it)."""
    if not settings.discord_webhook_url:
        log.info("notifier_fake", note="DISCORD_WEBHOOK_URL not set: messages are only logged")
        return FakeNotifier()
    admin_url = settings.discord_admin_webhook_url or settings.discord_webhook_url
    log.info("notifier_discord", separate_admin_channel=bool(settings.discord_admin_webhook_url))
    return DiscordNotifier(
        client, {Channel.LEADS: settings.discord_webhook_url, Channel.ADMIN: admin_url}
    )
