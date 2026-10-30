"""Where approved leads go: the `CRMClient` interface, and `FakeCRM` for tests and demos.

The `act` node calls `upsert_lead(key, fields)`. The key is the lead's thread id
(one per run item), so calling it twice for the same item updates one record instead
of creating two: that's what makes the CRM write safe to repeat after a crash.

Phase 8 adds the real Airtable client behind the same interface.
"""

from abc import ABC, abstractmethod
from typing import Any


class CRMClient(ABC):
    @abstractmethod
    def upsert_lead(self, key: str, fields: dict[str, Any]) -> str:
        """Create the record with this key, or update it if it exists. Returns its id."""


class FakeCRM(CRMClient):
    """Keeps records in a dict. `writes` counts every call (tests check "exactly once")."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.ids: dict[str, str] = {}
        self.writes = 0

    def upsert_lead(self, key: str, fields: dict[str, Any]) -> str:
        self.writes += 1
        self.records[key] = fields
        return self.ids.setdefault(key, f"fake-crm-{len(self.ids) + 1}")
