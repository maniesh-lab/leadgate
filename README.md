# leadgate

[![CI](https://github.com/maniesh-lab/leadgate/actions/workflows/ci.yml/badge.svg)](https://github.com/maniesh-lab/leadgate/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/maniesh-lab/leadgate/branch/main/graph/badge.svg)](https://codecov.io/gh/maniesh-lab/leadgate)

**Lead lists found and checked by AI agents, and nothing reaches your CRM until a person approves it.**

<p align="center"><img src="docs/demo.gif" alt="The demo: log in, open the approval queue, review a dentist's score breakdown and approve it, then see the decision in the audit log" width="720"></p>

**Live demo: https://leadgate-ocu8.onrender.com** (log in with the demo button, or `demo@leadgate.example` / `approve-some-leads`). It's on a free plan, so the first visit after a quiet spell takes about a minute to wake up.

A small web-design agency wants new clients: dentists in Austin whose websites look old or are missing. Buying or scraping a list gets you a mess: duplicates, wrong categories, closed businesses, phone numbers in five formats, no emails. And nobody wants an AI pushing that junk straight into their CRM.

leadgate does the boring part with agents and keeps a person in charge of what goes out. You give it a niche and a city ("dentist", "Austin, TX"). It pulls the businesses from OpenStreetMap (or Google Places, or a CSV you upload), visits each one's website for public emails and signs of an old site, cleans and dedupes everything, and scores each lead with *your* rules (good reviews + old or missing website = hot lead). Then it stops. The owner spends 15 minutes in a dashboard approving the best ones; approved leads land in Airtable and a Discord ping goes out. Every step is logged.

Each lead goes through these steps:

| Step | What it does | LLM? |
|---|---|---|
| `enrich` | Reads up to 4 pages (by default) of the lead's own website (robots.txt respected, public addresses only): emails, phones, social links, and site signals like https, a mobile layout and the copyright year | no |
| `extract` | Cleans the data by rule (phones to international format), then the LLM fills the fields that need reading. A check sends back any value that isn't in the source data or on the website, so it can't invent an email | yes |
| `check` | Flags possible duplicates, a category that doesn't match the campaign (close neighbours like hotel and guest house count as a match; a niche off the 119-type list shows "not checked"), and closed businesses | no |
| `score` | Adds up the campaign's rubric (rules stored as data). The LLM only writes a one-sentence explanation; it never changes the score | explanation only |
| `approval_gate` | Stops and waits for a person, for hours if needed | no |
| `route` + `act` | Approved and clean: write to Airtable + post to Discord. Approved but unsure (failed extraction, serious flags): escalate to an admin channel. Rejected: nothing | no |

Every lead in the queue shows its **reasons**: the score broken down rule by rule, which rubric scored it and who that rubric suits (so "+30 for no website" makes sense), the flags, what the website showed, and the full timeline of steps. The queue shows the newest leads first and can sort oldest first or by score. The audit log keeps who (or which agent) did what, and it can't be edited.

## How it works

```mermaid
flowchart LR
  UI["React dashboard"] -->|"REST + JWT"| API["FastAPI"]
  N8N["n8n (Switchyard)"] -->|"POST /api/webhooks/n8n/runs"| API
  API -->|"rows + jobs"| DB[("Postgres")]
  W["Worker"] -->|"claims jobs (SKIP LOCKED)"| DB
  W --> SRC["OpenStreetMap · Google Places · CSV"]
  W --> G["LangGraph graph (one thread per lead)"]
  G -->|"checkpoint after every node"| DB
  G -->|"robots.txt-aware crawl"| WEB["Business websites"]
  G -->|"LLM via Steadfast"| LLM["Groq / OpenAI / Claude"]
  G --> CRM["Airtable"]
  G --> D["Discord"]
  W -->|"results callback"| N8N
```

```mermaid
flowchart TD
  S([start]) --> enrich --> extract --> check --> score
  score --> gate["approval_gate (waits for a person)"]
  gate --> route
  route -->|skip| E([end])
  route -->|"notify / escalate"| act --> E
```

The API never runs agents itself. Starting a run writes a `runs` row and a job in one transaction; a worker claims jobs from a Postgres table and runs one LangGraph thread per lead. The graph saves a checkpoint after every node, so the worker can be killed at any moment and the next one carries on where it stopped. LLM calls go through [Steadfast](https://github.com/maniesh-lab/steadfast), my reliability layer for LLM APIs (retries, rate limits, validate-and-retry). The full design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Results

**Final end-to-end check** (2026-11-01, local Docker, real services: Groq, OpenStreetMap, the dentists' own websites, Airtable, Discord, Langfuse and n8n):

- A campaign for dentists in a Texas city collected 5 leads, crawled their sites, extracted and scored them, and parked them in the approval queue.
- The worker was killed while it was crawling a lead's website. After a restart it picked the job straight back up; **no step ran twice** for any lead (`python -m app.cli show-run` checks this).
- Decisions were made while the worker was stopped, and it handled them all after the restart. One approval went to Airtable (read back: every field) and Discord. One approval was escalated to the admin channel because the LLM's answer had been cut off. The rejections wrote nothing. A second decision on the same lead got a 409; a rejection without a reason got a 422.
- A run started from n8n went through the same queue, and when the last lead was decided, its summary was posted back to n8n.
- The audit log showed every step of every lead exactly once, from `lead.collected` to `crm.written` and `run.completed`.

**Extraction** (`backend/scripts/extraction_eval.py`, `openai/gpt-oss-20b` on Groq's free tier, 2026-11-08): on 20 real OpenStreetMap leads, email, area, city, country, category and founding year were right on 20/20; services 77% precision, 89% recall. Treat this as a smoke test, not proof: 12 of those 20 have no website, which makes most cases easy, and it's one run of one model. The eval has since grown to 30 leads, 18 of them with websites, including harder ones (a software firm tagged as a dentist, a wrong website, a placeholder email), and its services answers now list every service on each site. The leads and their saved site text are in the repo, so the eval runs without a database.

**SQL:** every index was measured with `EXPLAIN ANALYZE` on 50k leads and ~1M rows of history, with and without it. 14 were kept because they helped (e.g. one run's audit trail: 27 ms → 0.03 ms; the worker's job claim: 4.6 ms → 0.002 ms), and 2 were left out because they didn't. Details: [docs/sql-notes.md](docs/sql-notes.md).

## The live demo

[leadgate-ocu8.onrender.com](https://leadgate-ocu8.onrender.com) runs the real code on free hosting (Render for the API and the dashboard, Neon for Postgres). It logs you in as a reviewer, so you can work the approval queue, open any lead's step timeline, read the audit log and the dashboard's reports. The [admin tools](#admin-tools) need the admin login, which isn't public.

To keep it free and safe, the demo can't make **outside calls**, for any login, the admin's included: with `PUBLIC_DEMO=true` the API and worker refuse new or edited campaigns, uploads, runs and the n8n webhook, every outbound request is refused too, and the server won't start with a Google, Airtable, Discord or Langfuse key set. Real searches run on your own machine (see the quick start). On the demo, the LLM runs in fake mode (rule-based answers, the same checks), the leads are 12 made-up Austin businesses (11 dentists and a pharmacy), 7 of them with `.example` websites, and approved leads go to an in-memory CRM instead of Airtable. The queue refills itself when it runs low. The dashboard's history is 1,000 fake leads.

## Quick start (Docker)

You need Docker with the compose plugin. Running it on your own machine to try it out needs no permission; using it for business work does (see [License](#license)).

```bash
git clone https://github.com/maniesh-lab/leadgate
cd leadgate
cp .env.example .env
```

In `.env`, set three values: `JWT_SECRET` (make one with `openssl rand -hex 32`), `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Then:

```bash
docker compose up --build
```

The first start builds the images, creates the tables, and adds the admin and the two scoring rubrics. Then:

- **Dashboard:** open http://localhost:8080 and log in as the admin. Create a campaign (e.g. "dentist", "Austin, TX", source OpenStreetMap, rubric `web-agency`), press **Start run**, and the leads arrive in the **Queue** as the worker gets through them (set **Max results** to 5 for a quick first try).
- **API docs:** http://localhost:8000/docs (log in with the **Authorize** button).

Out of the box the LLM runs in fake mode and approved leads stay in memory, but the lead sources and website visits are real (they need internet). Add keys to `.env` to turn on the rest: `LLM_MODE=groq` + `GROQ_API_KEY` (free at [console.groq.com](https://console.groq.com)), or `LLM_MODE=anthropic` + `ANTHROPIC_API_KEY` for Claude (paid, more reliable; Claude Haiku 5.5 by default), the three `AIRTABLE_*` values, the `DISCORD_*` webhooks, `N8N_WEBHOOK_SECRET`, the `LANGFUSE_*` keys. Each setting is explained in [.env.example](.env.example).

## Run it without Docker (for development)

Needs Python 3.14 and Node 26.

```bash
python -m venv .venv && source .venv/bin/activate     # fish: source .venv/bin/activate.fish
pip install -e "./backend[dev]"
docker compose up -d db                                 # just the database
cd backend
alembic upgrade head
python -m app.cli seed                                  # admin + rubrics (from .env)
uvicorn app.main:create_app --factory --reload          # API on http://localhost:8000
python -m app.worker                                    # the worker, in a second terminal
```

For this, `.env` needs `DATABASE_URL` (e.g. `postgresql+psycopg://leadgate:leadgate@localhost:5432/leadgate`) as well as `JWT_SECRET`, `ADMIN_EMAIL` and `ADMIN_PASSWORD`. The dashboard:

```bash
cd frontend
npm install
npm run dev                                             # http://localhost:5173, /api goes to :8000
```

Other useful commands (from `backend/`):

```bash
python -m app.cli collect --campaign 1                  # run a campaign's sources only
python -m app.cli start-run --campaign 1                # queue a full run
python -m app.cli decide --item 12 --decision rejected --reason "a chain, not a small practice"
python -m app.cli show-run --run 3                      # items, statuses, and any step that ran twice
python -m app.cli opt-out --lead 42                     # wipe a lead's contact data for good
python -m app.cli ingest leads.csv --mapping mapping.json   # import any CSV
```

## Admin tools

Admins get a few extra tools in the dashboard. Each one is admin-only, and each one writes an audit row. On the public demo, uploading is off with the rest of the lockdown.

- **Upload a CSV** of your own leads on the New campaign page (UTF-8 with a header row, up to 1 MB by default, `CSV_UPLOAD_MAX_BYTES`). The file is stored in Postgres, so every worker reads the same copy; the API never takes a file path on the server.
- **Export approved leads** as a CSV from a run's page or a campaign's row. Escalated approvals are included, with a `route` column to tell them apart; opted-out leads are left out; a campaign gives one row per lead; a cell that starts like a spreadsheet formula gets a leading `'` so it can't run in Excel.
- **Users** (Settings → Users): list the accounts, add a reviewer, turn an account off or back on. Turning one off ends its logins at once, and turning it back on doesn't bring the old ones back. You can't turn off yourself or the last active admin.
- **Delete** a lead, a run or a campaign from its page, and an uploaded CSV with its campaign. A run takes the leads that are in no other run; a campaign takes its runs and its file when no other campaign uses it. A delete is refused while the worker is still on something. The audit log keeps its rows, with names, contact details and free text blanked. Anything already sent to Airtable or Discord stays there, and a later run may find a deleted lead again: to keep a lead out for good, opt it out instead.
- **Retry a failed lead** from its run item page. It goes through the job queue like everything else and continues from the lead's last checkpoint, so steps that already finished don't run again and the CRM write still happens once. A second click gets a 409.

## API

All under `/api`. Any login can read (except the user list and the exports); admins set things up; reviewers (and admins) decide.

| Method | Path | Who | What it does |
|---|---|---|---|
| `POST` | `/auth/login` | anyone | OAuth2 form (`username` is your email, plus `password`) → a login token (60 min) |
| `GET` | `/auth/me` | logged in | Who you are |
| `POST` | `/auth/password` | logged in | Change your password; ends every older token |
| `GET`, `POST` | `/users` | admin | List users, add a reviewer |
| `PATCH` | `/users/{id}` | admin | `{is_active}`: turn an account off or on |
| `GET`, `POST`, `PATCH` | `/campaigns`, `/campaigns/{id}` | admin to change | Niche, location, sources, rubric |
| `POST` | `/campaigns/{id}/runs` | admin | Start a run |
| `POST` | `/uploads/csv` | admin | Upload a CSV of leads (the body is the file); a campaign uses it by id |
| `GET` | `/runs`, `/runs/{id}`, `/run-items/{id}` | logged in | Runs, their leads, each lead's step timeline |
| `GET` | `/approvals/queue` | reviewer | Leads waiting for a decision, newest first (`?sort=` `created_at` for oldest, `-score` for highest) |
| `POST` | `/run-items/{id}/decision` | reviewer | `{decision, reason}`; 409 if someone already decided |
| `POST` | `/run-items/{id}/retry` | admin | Queue a failed lead again; 409 if it isn't failed |
| `GET` | `/leads`, `/leads/{id}` | logged in | Filter, sort, search, pages |
| `DELETE` | `/leads/{id}/data` | admin | Opt-out: wipe the lead's contact data |
| `DELETE` | `/leads/{id}`, `/runs/{id}`, `/campaigns/{id}`, `/uploads/csv/{id}` | admin | Delete; 409 while the worker is still on it (or a campaign still uses the file) |
| `GET` | `/exports/approved-leads` | admin | `?run_id=` or `?campaign_id=`: the approved leads as a CSV |
| `GET` | `/audit` | logged in | The audit trail, with filters (login, account, campaign, upload and export records: admins only) |
| `GET`, `POST` | `/rubrics`, `/rubrics/{id}` | admin to change | Scoring rules; an edit makes a new version |
| `GET` | `/reports`, `/reports/{name}` | logged in | The dashboard's SQL reports: the list, and one report |
| `POST` | `/webhooks/n8n/runs` | `X-Webhook-Secret` | n8n starts a run |
| `GET` | `/health`, `/ready` | anyone | `/ready` also checks the database |

Errors share one JSON shape, and lists come as `{items, total, page, page_size}`. Every response carries an `X-Request-ID`, and the same id is on every log line, job and audit row of that request's run.

## Human approval and crash safety

- **The gate:** the graph stops at `approval_gate` with LangGraph's `interrupt()` and is saved in Postgres. The node has no side effects, because LangGraph runs it again from the top when it resumes.
- **One decision, one transaction:** the run item's row is locked, its status checked (409 if it's no longer waiting), then the approval, the audit row and a resume job are saved together. A unique key on `approvals.run_item_id` is a second guard.
- **Kill it anywhere:** a job left half-done by a dead worker goes back to the queue, and the handler asks LangGraph where the thread stopped, so finished steps never run again. The CRM write is keyed by the lead's thread id and the Discord message is stamped once sent, so a retry doesn't make a second record or message. This is tested by killing the worker in the Postgres tests and was checked by hand with `docker compose kill worker`.

## SQL reports

The dashboard is drawn from seven hand-written Postgres queries (`backend/app/reporting/queries.py`), each tested against a hand-built dataset with exact numbers:

| Report | Shows | SQL |
|---|---|---|
| `daily_funnel` | collected / approved / rejected / escalated per day | `generate_series`, `count(*) FILTER` |
| `approved_running_total` | approvals per day and the running total | `sum() OVER (ORDER BY day)` |
| `top_leads_per_area` | the best lead in each city and area | `RANK() OVER (PARTITION BY ...)` |
| `reviewer_stats` | decisions, approval %, median wait per reviewer | `percentile_cont(0.5) WITHIN GROUP` |
| `node_durations` | average and p95 time per agent step | `percentile_cont(0.95)`, `FILTER` |
| `score_distribution` | leads per score bucket | `CASE` buckets, a `VALUES` list for empty ones |
| `campaign_conversion` | collected → approved, % with email, % duplicates | `bool_or`, `jsonb_array_elements_text` |

Which indexes were kept, which weren't, and why: [docs/sql-notes.md](docs/sql-notes.md).

## Integrations

- **Airtable** (the CRM): approved leads go to a table with 12 fields (thread id as the key, name, phone, email, website, address, city, category, score, explanation, lead id, run item id; the list is in `backend/app/services/crm.py`). Behind a `CRMClient` interface, so a real CRM is one more class.
- **Discord:** a webhook per channel, one for approved leads and one for escalations. Mentions are switched off, so a lead's name can't ping anyone.
- **n8n** ([Switchyard](https://github.com/maniesh-lab/Switchyard), my self-hosted n8n): a workflow starts a run through the webhook with a shared secret, and leadgate posts the results back when the run is done. The workflow is in [n8n/leadgate.json](n8n/leadgate.json); both run on a shared Docker network (`leadnet`).
- **Langfuse** (optional): with keys set, each lead is one trace, with a span per step and every LLM call's prompt, answer and tokens. Off without keys.

## Tests

```bash
cd backend
pytest                                   # 545 unit tests on SQLite, fake LLM, no network (the Postgres ones skip)
TEST_POSTGRES_URL=postgresql+psycopg://leadgate:leadgate@localhost:5432/leadgate pytest tests/integration
ruff check . && ruff format --check .
cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build
npm run check:api                        # schema.d.ts still matches the API (needs the backend installed, not running; gen:api remakes it)
npm run e2e:up && npm run e2e && npm run e2e:down    # Playwright on its own Docker stack
```

The 27 Postgres tests each make and drop their own database on the server you point them at. No test calls a real outside service: the LLM runs in fake mode, and Airtable, Discord, n8n, Google, OpenStreetMap and the websites are faked or replayed from saved answers.

CI runs four jobs on every pull request and every push to `main`: backend unit tests with coverage (uploaded to Codecov), the Postgres tests on a real Postgres 18, the frontend checks (lint, types, 70 Vitest tests, build, and that the API types match the API), and the end-to-end test (the whole stack in Docker, a reviewer logs in, approves a lead and finds it in the audit log).

## Project structure

```
backend/
  app/
    main.py  config.py  cli.py     FastAPI app, settings, the command-line tools
    api/routes/                    one file per resource (auth, campaigns, runs, approvals, ...)
    agents/                        the LangGraph graph, the 7 nodes, scoring, cleaning, llm.py
    enrichment/                    the website crawler and what it reads from a page
    sources/                       OpenStreetMap, Google Places, CSV, n8n's inline leads
    services/                      runs, approvals, audit, CRM, Discord, n8n callback, opt-out
    worker/                        the job queue and the worker loop
    reporting/queries.py           the SQL reports
    db/                            models, sessions, seed, demo and fake data
    observability/                 JSON logs, correlation ids, Langfuse
  alembic/                         migrations
  tests/unit/  tests/integration/  SQLite tests, Postgres tests
  scripts/                         live checks, the extraction eval, the SQL index bench, the OpenAPI export
  eval/                            the extraction eval's leads (with their saved site text) and checked answers
frontend/                          React + TypeScript + Vite + Tailwind; e2e/ = Playwright
docs/ARCHITECTURE.md               how it fits together (diagrams)
docs/sql-notes.md                  indexes and reports, with measurements
n8n/leadgate.json                  the n8n workflow
docker-compose.yml                 db + migrate + api + worker + frontend
render.yaml                        the live demo's hosting
```

## Settings

All settings come from `.env`; [.env.example](.env.example) explains each one. The main ones: `LLM_MODE` (`fake`, `groq`, `openai` or `anthropic`), `LLM_MODEL` (any model of that provider; e.g. `claude-sonnet-5-5` or `claude-opus-5-5` for a stronger Claude), `LLM_REASONING_EFFORT` (how long the model thinks: `low` by default; on Groq `medium` finds services better for about twice the tokens), `LLM_TOKENS_PER_MINUTE` (empty = 8,000 on Groq, its free tier's limit), `GOOGLE_PLACES_API_KEY` (empty = OpenStreetMap only), `OVERPASS_BACKUP_URL` (a second Overpass server for when the public one is busy; `NOMINATIM_URL` and `OVERPASS_URL` change the main ones), `ENRICH_CACHE_DAYS` and `ENRICH_MAX_PAGES` (how often and how deep websites are read), `CSV_UPLOAD_MAX_BYTES` (the biggest CSV an admin can upload), `PUBLIC_DEMO` (the live demo's lockdown; leave it off on your machine), `RUN_WORKER_IN_API` (one process for free hosting), and the optional Airtable, Discord, n8n and Langfuse keys. The optional services are off when their keys are empty. OpenStreetMap and website visits need no keys, so they stay on.

## Design decisions

- **Postgres as the job queue** (`FOR UPDATE SKIP LOCKED`), not Celery + Redis: a job is saved in the same transaction as the rows it belongs to, and there's one less service to run.
- **One LangGraph thread per lead:** leads pause, resume and fail on their own, and a reviewer can take days on one without holding up the rest.
- **Rules decide, the LLM explains:** the score and the route are plain rules; the LLM only fills fields it can prove (the traceability check) and writes explanations. A lead is never approved, rejected or scored by a model.
- **Scoring rubrics are data**, versioned in the database, so each client gets their own rules without code changes.
- **Fake mode for the LLM:** the same code path with rule-based answers, so tests, CI, the end-to-end test and the public demo need no API key and never call out.
- **SQLite for unit tests, Postgres for everything else:** the models work on both; the Postgres-only parts (the audit trigger, trigram search, the reports) have their own tests that run in CI.
- **The login token is stored in the browser's localStorage:** simple, and the strict Content-Security-Policy (only the app's own scripts) limits what an injected script could do. The trade-off: an XSS bug could read it.
- **Free hosting that sleeps** (Render + Neon) instead of a paid server: the demo costs nothing, and the worker runs inside the API process there.

## Security

Passwords are hashed with Argon2; logins are 60-minute JWTs, and changing your password ends every older one. Every endpoint checks the role. Failed logins are audit rows: 5 for one email or 20 from one address in 15 minutes locks login for 15 minutes. The crawler refuses private and internal addresses on every request and redirect. The dashboard is served with a strict Content-Security-Policy, no framing and HSTS. A security review of the whole repo was done before this README (2026-11-01); its leftovers are in the list below.

## Known limitations

- **Google's terms don't allow what this app does with Places data.** Google's Maps Platform terms forbid storing Places content outside the service (they name copying and saving business names and addresses), and only the place ID, plus latitude and longitude for 30 days, may be kept. The `google_places` source saves those fields on the lead and an approved lead's fields go to the CRM, and its 30-day wipe doesn't fix that. It stays off without a key: OpenStreetMap or your own CSV are the sources to use for a real lead list. The dashboard shows the required "Google Maps" credit next to Google leads.
- **No ratings from OpenStreetMap.** OSM has no reviews, so on the `web-agency` rubric most OSM leads score 0–20. Google Places data scores higher, but its API needs a billing card even for the free tier.
- **The public Overpass server is often busy** (HTTP 502), slow, or can't be reached. A collect waits and tries again twice, and with `OVERPASS_BACKUP_URL` set it asks a second server straight away; with both busy, it may still need a second try later.
- **Fewer emails than you'd hope:** many sites hide their email behind Cloudflare's obfuscation, and leadgate leaves it hidden. Sites built only with JavaScript show the crawler little text (no headless browser, on purpose).
- **The default LLM is a small free model on a free tier** (a paid Claude or OpenAI key avoids these limits). Groq allows 8,000 tokens a minute, and leadgate waits for room instead of hitting the limit (`LLM_TOKENS_PER_MINUTE`). It also allows 200,000 tokens a day, which several evals and runs can use up. When the daily limit is reached, or an answer is still invalid after three tries, the lead is marked "extraction not ok" and escalated rather than guessed.
- **Two tiny crash windows remain:** a kill in the instant between two back-to-back commits could record a step twice or post a Discord message twice. Closing them would need an idempotency key at the provider, and Discord webhooks have none.
- **Login tokens can't be cut off one by one** before they expire (no refresh tokens or deny list); a password change or deactivating the user ends them.
- **The login limit can be used to lock someone out** for 15 minutes by typing their email wrong 5 times, and an admin can't unlock it early. No two-factor login.
- **The crawler's address check has a small DNS gap:** the address is looked up for the check and again to connect, so a DNS server that answers differently the second time could slip through.
- **The n8n results address isn't address-checked**, because it has to reach n8n on the private network; only someone with the shared secret can set it.
- **An opted-out lead's old data can stay in a LangGraph checkpoint** when the opt-out comes from the command line while the lead waits for a decision (approving it is blocked). Through the dashboard or the API, the waiting lead is rejected and its checkpoint deleted once it finishes, and its audit rows are blanked.
- **An uploaded CSV is kept in the database as it was uploaded** until an admin deletes it, so an opt-out doesn't wipe the lead's row in that file.
- **The demo's admin still sees New campaign and Start run.** On the public demo they answer with a message that real searches run on your own machine, rather than being hidden.
- **The demo's public reviewer login** can read everything a reviewer can (made-up data; no login, account, campaign, upload or export records), and the live API's docs page is public (every endpoint still needs a login).
- **Small hardening left undone:** the API's own JSON answers carry no security headers (the live API is HTTPS-only anyway: `onrender.com` is on the browsers' HSTS preload list), the local nginx shows its version, and the local database password defaults to `leadgate` (its port only listens on 127.0.0.1).
- **With Langfuse or LLM keys set, lead data leaves your server:** the LLM prompts and a cut-down copy of each lead go to those services.

## Legal and ethics

- **Official APIs only:** OpenStreetMap through Overpass and Nominatim within their usage policies (an identifying User-Agent, rate limits, caching), and Google Places through its official API, never by scraping Google Maps. Google's terms are stricter than this app's storage, so read the known limit above before using a Google key.
- **Websites:** robots.txt is obeyed, every request names leadgate in its User-Agent, a site gets at most one request per second and 4 pages by default, and there are no login walls, CAPTCHA solving or proxies.
- **Public business data only:** a business's own listing and public website. Any lead can be opted out: its contact data is wiped and it's never collected or crawled again.
- **Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)** (ODbL 1.0), also credited in the dashboard's footer. The ODbL covers OpenStreetMap's data, not leadgate's code; the few saved Overpass answers in the tests are small extracts.

## License

Not open source. You may read and review the code, and run or change your own private copy to test it, without asking. Using it, or the leads it finds, for a business, a client or paid work needs written approval, and so do hosting it for others and sharing a changed copy; the details are in [LICENSE](LICENSE). Third-party parts keep their own licences (e.g. the bundled fonts are SIL OFL). Built by [Manish Pandeya](https://github.com/maniesh-lab).
