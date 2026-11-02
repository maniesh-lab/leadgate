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
| `enrich` | Reads up to 4 pages of the lead's own website (robots.txt respected, public addresses only): emails, phones, social links, and site signals like https, a mobile layout and the copyright year | no |
| `extract` | Cleans the data by rule (phones to international format), then the LLM fills the fields that need reading. A check sends back any value that isn't in the source data or on the website, so it can't invent an email | yes |
| `check` | Flags possible duplicates, a category that doesn't match the campaign, and closed businesses | no |
| `score` | Adds up the campaign's rubric (rules stored as data). The LLM only writes a one-sentence explanation; it never changes the score | explanation only |
| `approval_gate` | Stops and waits for a person, for hours if needed | no |
| `route` + `act` | Approved and clean: write to Airtable + post to Discord. Approved but unsure (failed extraction, serious flags): escalate to an admin channel. Rejected: nothing | no |

Every lead in the queue shows its **reasons**: the score broken down rule by rule, the flags, what the website showed, and the full timeline of steps. The audit log keeps who (or which agent) did what, and it can't be edited.

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
  G -->|"LLM via Steadfast"| LLM["Groq / OpenAI"]
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

**Extraction** (`backend/scripts/extraction_eval.py`, 20 real OpenStreetMap leads, `openai/gpt-oss-20b` on Groq's free tier): email, area, city, country, category and founding year right on 20/20; services 86% precision, 93% recall. Treat this as a smoke test, not proof: 12 of the 20 leads have no website, which makes most cases easy, and it's one run of one model.

**SQL:** every index was measured with `EXPLAIN ANALYZE` on 50k leads and ~1M rows of history, with and without it. 14 were kept because they helped (e.g. one run's audit trail: 27 ms → 0.03 ms; the worker's job claim: 4.6 ms → 0.002 ms), and 2 were left out because they didn't. Details: [docs/sql-notes.md](docs/sql-notes.md).

## The live demo

[leadgate-ocu8.onrender.com](https://leadgate-ocu8.onrender.com) runs the real code on free hosting (Render for the API and the dashboard, Neon for Postgres). It logs you in as a reviewer, so you can work the approval queue, open any lead's step timeline, read the audit log and the dashboard's reports. The [admin tools](#admin-tools) need the admin login, which isn't public.

To keep it free and safe, the demo makes **no outside calls**: the LLM runs in fake mode (rule-based answers, the same checks), the leads are 12 made-up Austin dentists with `.example` websites, and approved leads go to an in-memory CRM instead of Airtable. The queue refills itself when it runs low. The dashboard's history is 1,000 fake leads.

## Quick start (Docker)

You need Docker with the compose plugin.

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

Out of the box the LLM runs in fake mode and approved leads stay in memory, but the lead sources and website visits are real (they need internet). Add keys to `.env` to turn on the rest: `LLM_MODE=groq` + `GROQ_API_KEY` (free at [console.groq.com](https://console.groq.com)), the three `AIRTABLE_*` values, the `DISCORD_*` webhooks, `N8N_WEBHOOK_SECRET`, the `LANGFUSE_*` keys. Each setting is explained in [.env.example](.env.example).

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

Admins get a few extra tools in the dashboard. Each one is admin-only, and each one writes an audit row.

- **Upload a CSV** of your own leads on the New campaign page (UTF-8 with a header row, up to 1 MB by default, `CSV_UPLOAD_MAX_BYTES`). The file is stored in Postgres, so every worker reads the same copy; the API never takes a file path on the server.
- **Export approved leads** as a CSV from a run's page or a campaign's row. Escalated approvals are included, with a `route` column to tell them apart; opted-out leads are left out; a campaign gives one row per lead; a cell that starts like a spreadsheet formula gets a leading `'` so it can't run in Excel.
- **Users** (Settings → Users): list the accounts, add a reviewer, turn an account off or back on. Turning one off ends its logins at once, and turning it back on doesn't bring the old ones back. You can't turn off yourself or the last active admin.
- **Retry a failed lead** from its run item page. It goes through the job queue like everything else and continues from the lead's last checkpoint, so steps that already finished don't run again and the CRM write still happens once. A second click gets a 409.

## API

All under `/api`. Any login can read (except the user list); admins set things up; reviewers (and admins) decide.

| Method | Path | Who | What it does |
|---|---|---|---|
| `POST` | `/auth/login` | anyone | Email + password → a login token (60 min) |
| `GET` | `/auth/me` | logged in | Who you are |
| `POST` | `/auth/password` | logged in | Change your password; ends every older token |
| `GET`, `POST` | `/users` | admin | List users, add a reviewer |
| `PATCH` | `/users/{id}` | admin | `{is_active}`: turn an account off or on |
| `GET`, `POST`, `PATCH` | `/campaigns`, `/campaigns/{id}` | admin to change | Niche, location, sources, rubric |
| `POST` | `/campaigns/{id}/runs` | admin | Start a run |
| `POST` | `/uploads/csv` | admin | Upload a CSV of leads (the body is the file); a campaign uses it by id |
| `GET` | `/runs`, `/runs/{id}`, `/run-items/{id}` | logged in | Runs, their leads, each lead's step timeline |
| `GET` | `/approvals/queue` | reviewer | Leads waiting for a decision, oldest first |
| `POST` | `/run-items/{id}/decision` | reviewer | `{decision, reason}`; 409 if someone already decided |
| `POST` | `/run-items/{id}/retry` | admin | Queue a failed lead again; 409 if it isn't failed |
| `GET` | `/leads`, `/leads/{id}` | logged in | Filter, sort, search, pages |
| `DELETE` | `/leads/{id}/data` | admin | Opt-out: wipe the lead's contact data |
| `GET` | `/exports/approved-leads` | admin | `?run_id=` or `?campaign_id=`: the approved leads as a CSV |
| `GET` | `/audit` | logged in | The audit trail, with filters (login records: admins only) |
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
pytest                                   # 456 unit tests on SQLite, fake LLM, no network (the Postgres ones skip)
TEST_POSTGRES_URL=postgresql+psycopg://leadgate:leadgate@localhost:5432/leadgate pytest tests/integration
ruff check . && ruff format --check .
cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build
npm run e2e:up && npm run e2e && npm run e2e:down    # Playwright on its own Docker stack
```

The 18 Postgres tests each make and drop their own database on the server you point them at. No test calls a real outside service: the LLM runs in fake mode, and Airtable, Discord, n8n, Google, OpenStreetMap and the websites are faked or replayed from saved answers.

CI runs four jobs on every pull request and every push to `main`: backend unit tests with coverage (uploaded to Codecov), the Postgres tests on a real Postgres 18, the frontend checks (lint, types, 49 Vitest tests, build), and the end-to-end test (the whole stack in Docker, a reviewer logs in, approves a lead and finds it in the audit log).

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
  scripts/                         live checks, the extraction eval, the SQL index bench
  eval/                            the extraction eval's checked answers
frontend/                          React + TypeScript + Vite + Tailwind; e2e/ = Playwright
docs/ARCHITECTURE.md               how it fits together (diagrams)
docs/sql-notes.md                  indexes and reports, with measurements
n8n/leadgate.json                  the n8n workflow
docker-compose.yml                 db + migrate + api + worker + frontend
render.yaml                        the live demo's hosting
```

## Settings

All settings come from `.env`; [.env.example](.env.example) explains each one. The main ones: `LLM_MODE` (`fake`, `groq` or `openai`), `GOOGLE_PLACES_API_KEY` (empty = OpenStreetMap only), `ENRICH_CACHE_DAYS` and `ENRICH_MAX_PAGES` (how often and how deep websites are read), `CSV_UPLOAD_MAX_BYTES` (the biggest CSV an admin can upload), `RUN_WORKER_IN_API` (one process for free hosting), and the optional Airtable, Discord, n8n and Langfuse keys. Every outside service is off when its keys are empty.

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

- **No ratings from OpenStreetMap.** OSM has no reviews, so on the `web-agency` rubric most OSM leads score 0–20. Google Places data scores higher, but its API needs a billing card even for the free tier.
- **The public Overpass server is often busy** (HTTP 502) or slow; a collect may need a second try.
- **Fewer emails than you'd hope:** many sites hide their email behind Cloudflare's obfuscation, and leadgate leaves it hidden. Sites built only with JavaScript show the crawler little text (no headless browser, on purpose).
- **The LLM is a small free model.** It sometimes cuts an answer off or gets rate-limited; the lead is then marked "extraction not ok" and escalated rather than guessed.
- **Two tiny crash windows remain:** a kill in the instant between two back-to-back commits could record a step twice or post a Discord message twice. Closing them would need an idempotency key at the provider, and Discord webhooks have none.
- **Login tokens can't be cut off one by one** before they expire (no refresh tokens or deny list); a password change or deactivating the user ends them.
- **The login limit can be used to lock someone out** for 15 minutes by typing their email wrong 5 times, and an admin can't unlock it early. No two-factor login.
- **The crawler's address check has a small DNS gap:** the address is looked up for the check and again to connect, so a DNS server that answers differently the second time could slip through.
- **The n8n results address isn't address-checked**, because it has to reach n8n on the private network; only someone with the shared secret can set it.
- **An opted-out lead's old data stays in its LangGraph checkpoint** (approvals are blocked, but the checkpoint isn't wiped).
- **The demo's public reviewer login** can read everything a reviewer can (made-up data, no login records), and the live API's docs page is public (every endpoint still needs a login).
- **Small hardening left undone:** the API's own JSON answers carry no security headers (the live API is HTTPS-only anyway: `onrender.com` is on the browsers' HSTS preload list), the local nginx shows its version, and the local database password defaults to `leadgate` (its port only listens on 127.0.0.1).
- **With Langfuse or Groq keys set, lead data leaves your server:** the LLM prompts and a cut-down copy of each lead go to those services.

## Legal and ethics

- **Official APIs only:** OpenStreetMap through Overpass and Nominatim within their usage policies (an identifying User-Agent, rate limits, caching), and Google Places through its official API, never by scraping Google Maps. Cached Google content other than the place id and name is wiped after 30 days.
- **Websites:** robots.txt is obeyed, every request names leadgate in its User-Agent, a site gets at most one request per second and 4 pages, and there are no login walls, CAPTCHA solving or proxies.
- **Public business data only:** a business's own listing and public website. Any lead can be opted out: its contact data is wiped and it's never collected or crawled again.
- **Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)** (ODbL 1.0), also credited in the dashboard's footer. The ODbL covers OpenStreetMap's data, not leadgate's code; the few saved Overpass answers in the tests are small extracts.

## License

All rights reserved. The code is public so it can be read and reviewed, not reused; see [LICENSE](LICENSE). Third-party parts keep their own licences (e.g. the bundled fonts are SIL OFL). Built by [Manish Pandeya](https://github.com/maniesh-lab).
