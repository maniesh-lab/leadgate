"""The declarative base and the column types every model shares.

`models.py` builds every table on `Base`. The helpers here keep the models
portable: the same code runs on Postgres (real) and SQLite (unit tests).

- `JSONType`: JSONB on Postgres, plain JSON elsewhere.
- `UTCDateTime`: always hands back timezone-aware UTC datetimes, even on SQLite
  (which has no timezone support and would otherwise return naive ones).
- `str_enum()`: stores a Python `StrEnum` as a plain string column plus a CHECK
  constraint, instead of a native Postgres enum type (easier to migrate).

The naming convention gives every index and constraint a predictable name,
which Alembic needs to drop or change them later.
"""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Dialect, Enum, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp column that only accepts and returns timezone-aware UTC datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given; use datetime.now(UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        # SQLite drops the timezone, so a naive value read back is UTC by our own rule above.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def str_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """A string column limited to the enum's values by a CHECK constraint named `name`."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        # Store the lowercase values ("admin"), not the Python member names ("ADMIN").
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
