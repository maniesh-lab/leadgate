"""Shared pytest fixtures.

Unit tests run on a throwaway SQLite file, so they need no Postgres and no network.
Every test gets its own fresh file (pytest's `tmp_path`), so tests never share data.
API tests log in with `as_admin` / `as_reviewer` (request headers with a valid token).
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401  (importing it registers every table on Base.metadata)
from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.db.base import Base
from app.db.models import ScoringRubric, User, UserRole
from app.db.seed import WEB_AGENCY_RULES
from app.db.session import make_engine, make_session_factory
from app.main import create_app

# Settings a developer may have exported in their shell; tests must not see them.
APP_ENV_VARS = [
    "DATABASE_URL",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "JWT_SECRET",
    "JWT_EXPIRE_MINUTES",
    "OSM_USER_AGENT",
    "GOOGLE_PLACES_API_KEY",
    "ENRICH_CACHE_DAYS",
    "ENRICH_MAX_PAGES",
    "LLM_MODE",
    "LLM_MODEL",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "RUN_WORKER_IN_API",
    "AIRTABLE_TOKEN",
    "AIRTABLE_BASE_ID",
    "AIRTABLE_TABLE",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_ADMIN_WEBHOOK_URL",
    "N8N_WEBHOOK_SECRET",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
]
# The password `add_user()` gives every test user.
PASSWORD = "password-123"
# Signs the tests' login tokens (32+ characters, like the real one must be).
TEST_JWT_SECRET = "test-secret-" + "x" * 32


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # _env_file=None: never read the developer's real .env, so tests act the same everywhere.
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        jwt_secret=TEST_JWT_SECRET,
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    """A SQLite engine with every table already created."""
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as session:
        yield session


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """The API on the test's SQLite file. Ask for `db` too when the test needs the tables."""
    # The `with` block runs the app's lifespan, which creates the DB engine.
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def admin(db: Session) -> User:
    return add_user(db, "admin@example.com", UserRole.ADMIN)


@pytest.fixture
def reviewer(db: Session) -> User:
    return add_user(db, "reviewer@example.com", UserRole.REVIEWER)


@pytest.fixture
def as_admin(admin: User) -> dict[str, str]:
    """Headers that make a request as the admin."""
    return auth_headers(admin)


@pytest.fixture
def as_reviewer(reviewer: User) -> dict[str, str]:
    return auth_headers(reviewer)


@pytest.fixture
def rubric(db: Session) -> ScoringRubric:
    """The seeded web-agency rubric, v1."""
    rubric = ScoringRubric(name="web-agency", version=1, rules=WEB_AGENCY_RULES)
    db.add(rubric)
    db.commit()
    return rubric


def add_user(db: Session, email: str, role: UserRole, password: str = PASSWORD) -> User:
    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return user


def auth_headers(user: User) -> dict[str, str]:
    """A valid token for `user`, made directly (no login request, no password check)."""
    token = create_access_token(user.id, TEST_JWT_SECRET, 60, datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}
