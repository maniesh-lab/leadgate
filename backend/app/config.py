"""App settings, read from environment variables (and the repo-root `.env` if present).

One `Settings` object holds every config value so the rest of the code never
reads `os.environ` directly. `create_app()`, the CLI and Alembic's `env.py` call
`get_settings()`, and so does the worker (`app.worker.main`). Every key here must also be
listed in `.env.example`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
    # The first admin, created by `python -m app.cli seed`. `decide` also uses ADMIN_EMAIL as
    # its default reviewer.
    admin_email: str | None = None
    # repr=False: never shown when the settings object is printed (e.g. in a traceback).
    admin_password: str | None = Field(default=None, repr=False)

    # Signs the API's login tokens (HS256). The API refuses to start without it; the CLI
    # and the worker don't need it. At least 32 characters: `openssl rand -hex 32`.
    jwt_secret: str | None = Field(default=None, min_length=32, repr=False)
    # How long a login token is valid. There are no refresh tokens: log in again after.
    jwt_expire_minutes: int = Field(default=60, ge=1)

    # Sent on every request leadgate makes: OSM's Nominatim and Overpass (their policies ask
    # for a User-Agent that names the app), Google Places, and the website crawler.
    osm_user_agent: str = DEFAULT_USER_AGENT
    # The `google_places` source only runs when this is set.
    google_places_api_key: str | None = Field(default=None, repr=False)

    # Website enrichment: reuse a lead's enrichment while it is younger than this.
    enrich_cache_days: int = Field(default=14, ge=0)
    # Pages fetched per website: the home page plus up to (this - 1) contact/about/... pages.
    enrich_max_pages: int = Field(default=4, ge=1)

    # Which LLM the agents use (see app/agents/llm.py): "fake" answers without any network
    # (tests, CI, offline demos), "groq" and "openai" call the real APIs through Steadfast.
    llm_mode: Literal["fake", "groq", "openai"] = "fake"
    # The model for groq/openai; empty = the default in app/agents/llm.py.
    llm_model: str | None = None
    groq_api_key: str | None = Field(default=None, repr=False)
    openai_api_key: str | None = Field(default=None, repr=False)

    # Run the job worker inside the API process (a background thread) instead of as its
    # own `python -m app.worker` process: for free hosting with a single process.
    run_worker_in_api: bool = False

    # The CRM approved leads go to (app/services/crm.py). All three set = Airtable;
    # otherwise the worker keeps them in memory (FakeCRM).
    airtable_token: str | None = Field(default=None, repr=False)
    airtable_base_id: str | None = None  # "app…"
    airtable_table: str | None = None  # the table's name or id ("tbl…")

    # Discord webhooks (app/services/notify.py): the leads channel, and the admin channel
    # for escalations (empty = escalations go to the leads channel too). No leads webhook =
    # messages are only logged (FakeNotifier). repr=False: the URL holds the webhook's token.
    discord_webhook_url: str | None = Field(default=None, repr=False)
    discord_admin_webhook_url: str | None = Field(default=None, repr=False)

    # Shared with Switchyard's n8n: n8n sends it as `X-Webhook-Secret` to start a run, and
    # leadgate sends it back with the results callback. Empty = the n8n webhook is off (503).
    n8n_webhook_secret: str | None = Field(default=None, min_length=32, repr=False)

    # Langfuse tracing (app/observability/tracing.py): on only when both keys are set.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = Field(default=None, repr=False)
    # The Langfuse server; the SDK's own name for it (LANGFUSE_HOST is the old one).
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # Websites allowed to call the API from a browser (CORS), comma-separated, e.g.
    # "https://leadgate.example.com". Empty = none: the dashboard reaches the API on its own
    # address (the Vite dev server passes `/api` through; the frontend's nginx will too).
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
