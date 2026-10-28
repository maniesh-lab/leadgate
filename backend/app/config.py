"""App settings, read from environment variables (and the repo-root `.env` if present).

One `Settings` object holds every config value so the rest of the code never
reads `os.environ` directly. `create_app()`, the CLI and Alembic's `env.py` call
`get_settings()` (the worker will too, from Phase 6). Every key here must also be
listed in `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repo root. Inside Docker this path has no .env, which is fine:
# compose passes the values as real environment variables instead.
REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


DEFAULT_USER_AGENT = "leadgate/0.1 (+https://github.com/maniesh-lab/leadgate)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT_ENV,
        extra="ignore",
        # A key left empty in .env (`KEY=`) means "use the default".
        env_ignore_empty=True,
        # Error messages never echo the values (they include passwords and keys).
        hide_input_in_errors=True,
    )

    database_url: str
    # The first admin, created by `python -m app.cli seed`. Only the seed command needs them.
    admin_email: str | None = None
    admin_password: str | None = None

    # Sent on every request to OpenStreetMap's Nominatim and Overpass (their policies ask
    # for a User-Agent that names the app).
    osm_user_agent: str = DEFAULT_USER_AGENT
    # The `google_places` source only runs when this is set.
    google_places_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
