"""Messages to people: the `Notifier` interface, and `FakeNotifier` for tests and demos.

Two channels: `leads` (a new approved lead) and `admin` (a lead that needs a second
look). The `act` node sends at most one message per run item: it records `notified`
in the graph state and skips the send when it's already true.

Phase 8 adds the Discord webhook notifier behind the same interface.
"""

from abc import ABC, abstractmethod
from enum import StrEnum


class Channel(StrEnum):
    LEADS = "leads"
    ADMIN = "admin"


class Notifier(ABC):
    @abstractmethod
    def send(self, channel: Channel, text: str) -> None:
        """Post one message to the channel."""


class FakeNotifier(Notifier):
    """Remembers every message in `sent` instead of posting it."""

    def __init__(self) -> None:
        self.sent: list[tuple[Channel, str]] = []

    def send(self, channel: Channel, text: str) -> None:
        self.sent.append((channel, text))
