"""audit_log redaction: let a delete blank a row's details, and nothing else.

The append-only trigger from the initial migration blocks every UPDATE and DELETE. A
deleted lead's audit rows must stay (who did what, and when), but its name, contact
data and source data have to go (`app.services.deletion`). So the trigger now lets one
kind of UPDATE through:

- the transaction has set `leadgate.audit_redaction` to `on` (`set_config(..., true)`,
  so it ends with the transaction), and
- only the `details` column changes.

DELETE stays blocked, as does any other UPDATE. Postgres only, like the trigger itself.

Revision ID: 7d4e2b9c1a60
Revises: 5c9e1a7b3d20
Create Date: 2026-11-08 12:00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d4e2b9c1a60"
down_revision: str | Sequence[str] | None = "5c9e1a7b3d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOW_REDACTION = """
CREATE OR REPLACE FUNCTION audit_log_block_changes() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE'
        AND current_setting('leadgate.audit_redaction', true) = 'on'
        AND to_jsonb(NEW) - 'details' = to_jsonb(OLD) - 'details'
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP;
END;
$$
"""

BLOCK_EVERYTHING = """
CREATE OR REPLACE FUNCTION audit_log_block_changes() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP;
END;
$$
"""


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(ALLOW_REDACTION)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(BLOCK_EVERYTHING)
