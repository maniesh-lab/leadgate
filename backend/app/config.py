"""App settings, read from environment variables (and the repo-root `.env` if present).

One `Settings` object holds every config value so the rest of the code never
reads `os.environ` directly. `create_app()` and, later, the worker and CLI call
`get_settings()`. Every key here must also be listed in `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repo root. Inside Docker this path has no .env, which is fine:
# compose passes the values as real environment variables instead.
REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT_ENV, extra="ignore")

    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
