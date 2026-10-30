# SQL notes: indexes and reports

Every index in `backend/alembic/versions/2026_10_30_3ec64e25a3e3_indexes_backed_by_explain_analyze.py`
is here with the query it serves, its plan without and with it, and the timing. Indexes that
were measured and **left out** are here too, with the reason.

## How it was measured

- **Data:** `python scripts/sql_explain.py load --leads 50000` builds a separate database
  (`leadgate_sqlbench`) with `fake-data --leads 50000 --history`: every lead gets a run
  item with the steps, approval, audit rows and jobs the worker would write.

  | table | rows |
  |---|---|
  | leads | 50,000 |
  | runs | 2,184 |
  | run_items | 50,000 |
  | run_steps | 320,485 |
  | approvals | 48,943 |
  | audit_log | 495,071 |
  | jobs | 101,127 |

- **Queries:** the API's real statements. The routes build them with small functions
  (`leads_query()`, `queue_query()`, `audit_query()`, `steps_query()`, ...), and the script
  compiles those same functions, so what is measured is what the API runs.
- **Method:** `python scripts/sql_explain.py explain`. Each case runs twice, each time in
  a transaction that is rolled back: once with its index dropped, once with it created
  (then `ANALYZE`). `EXPLAIN (ANALYZE, BUFFERS)`, one warm-up run and the fastest of 5
  runs. Postgres 18.6 in Docker on a laptop, warm cache. Times are small in absolute terms
  (it's 50k leads), so the **plan shape** matters more than the milliseconds: a scan of the
  whole table grows with the data; an index lookup barely does.
- Plans below are cut to the lines that matter.

## Summary

| query | index | without | with | kept? |
|---|---|---|---|---|
| approval queue, page 1 | `ix_run_items_awaiting_approval` (partial) | 2.17 ms | 0.18 ms | yes (Phase 2) |
| approval queue, total | same | 1.99 ms | 0.31 ms | yes |
| worker claim | `ix_jobs_queued_run_after` (partial) | 4.62 ms | 0.002 ms | yes (Phase 2) |
| run's items | `ix_run_items_run_id` | 0.030 ms | 0.033 ms | **no** |
| lead's history | `ix_run_items_lead_id` | 0.45 ms | 0.046 ms | yes |
| leads in a campaign | `ix_runs_campaign_id` | 2.28 ms | 1.77 ms | **no** |
| run item's step timeline | `ix_run_steps_run_item_id_started_at` | 7.22 ms | 0.007 ms | yes |
| audit log of a run | `ix_audit_log_run_id` | 27.0 ms | 0.033 ms | yes |
| audit log of a lead | `ix_audit_log_entity_type_entity_id` | 13.7 ms | 0.008 ms | yes |
| audit log, newest | `ix_audit_log_occurred_at` | 17.6 ms | 0.018 ms | yes |
| audit log, last 24 h | same | 11.2 ms | 0.027 ms | yes |
| audit log, last 24 h total | same | 10.8 ms | 0.39 ms | yes |
| leads, newest first | `ix_leads_first_seen_at` | 6.64 ms | 0.027 ms | yes |
| leads, best score first | `ix_leads_score` (DESC NULLS LAST) | 6.80 ms | 0.18 ms | yes |
| leads, `status=in_review` | `ix_leads_status` | 5.93 ms | 0.036 ms | yes |
| leads, `status=approved` | same | 0.056 ms | 0.060 ms | (not used) |
| leads, `q=` name search | `ix_leads_name_trgm` (GIN) | 1.91 ms | 0.65 ms | yes |
| leads, `q=` total | same | 19.2 ms | 0.62 ms | yes |
| duplicate check | `ix_leads_source_ref` + `ix_leads_phone` + `ix_leads_website_domain` | 7.66 ms | 0.011 ms | yes |
| report: daily funnel | `ix_audit_log_occurred_at` | 37.8 ms | 37.8 ms | (no help) |
| report: reviewer stats | `ix_run_steps_run_item_id_started_at` | 78.8 ms | 77.2 ms | (no help) |

## Kept

### `ix_run_items_awaiting_approval` (partial, Phase 2): the approval queue

`GET /api/approvals/queue`: `WHERE run_items.status = 'awaiting_approval' ORDER BY created_at, id LIMIT 50`.
Only 84 of 50,000 items are waiting, so a partial index holds just those rows.

```
without (2.17 ms):  Seq Scan on run_items  Filter: status = 'awaiting_approval'  Rows Removed by Filter: 49916
with    (0.18 ms):  Index Scan using ix_run_items_awaiting_approval on run_items (rows=51)
```

The queue stays small while the table grows with every run, so the gap only widens.

### `ix_jobs_queued_run_after` (partial, Phase 2): the worker's claim

`SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED`,
every 1–2 s per worker. 101k jobs, none queued.

```
without (4.62 ms):  Seq Scan on jobs  Rows Removed by Filter: 101127
with    (0.002 ms): Index Only Scan using ix_jobs_queued_run_after on jobs (rows=0)
```

Finished jobs pile up forever; the index only holds queued ones, so the claim stays instant.

### `ix_run_items_lead_id`: a lead's history

`GET /api/leads/{id}`: the lead's run items with their campaign and decision, `WHERE run_items.lead_id = :id`.
The unique key `(run_id, lead_id)` starts with `run_id`, so it can't serve a lookup by lead.

```
without (0.45 ms):  ... Seq Scan on run_items (filter lead_id = 232)
with    (0.046 ms): Index Scan using ix_run_items_lead_id on run_items  Index Cond: lead_id = 232
```

Small now, but it's a full scan of `run_items` per lead page, and `run_items` grows with
every run. The same lookup runs in the opt-out route (the lead's waiting items), and it
backs the `?campaign_id=` filter (below).

### `ix_run_steps_run_item_id_started_at`: a run item's step timeline

`GET /api/run-items/{id}`: `WHERE run_item_id = :id ORDER BY started_at, id`, and the score
breakdown look-up (`reviews_for()`, `run_item_id IN (...)`) on every approval queue page.

```
without (7.22 ms):  Parallel Seq Scan on run_steps  Filter: run_item_id = 25041  Rows Removed by Filter: 106826 (x3 workers)
with    (0.007 ms): Index Scan using ix_run_steps_run_item_id_started_at  Index Cond: run_item_id = 25041
```

Both columns: the index hands the rows back already in `started_at` order.

### `ix_audit_log_run_id`: one run's audit trail

`GET /api/audit?run_id=`: `WHERE run_id = :id ORDER BY occurred_at DESC, id DESC LIMIT 50`.

```
without (27.0 ms):  Index Scan Backward using ix_audit_log_occurred_at  Filter: run_id = 1093  Rows Removed by Filter: 247051
with    (0.033 ms): Index Scan using ix_audit_log_run_id  Index Cond: run_id = 1093 (rows=168), top-N sort
```

A good example of a bad plan: without it, Postgres walks the time index from the newest
row and throws away 247k rows until it has found this (mid-history) run's 50.

### `ix_audit_log_entity_type_entity_id`: one lead's (or item's) story

`GET /api/audit?entity_type=lead&entity_id=`.

```
without (13.7 ms):  Parallel Seq Scan on audit_log  Rows Removed by Filter: 165020 (x3)
with    (0.008 ms): Index Scan using ix_audit_log_entity_type_entity_id  Index Cond: entity_type = 'lead' AND entity_id = 232
```

`entity_type` first: the filter always gives both, and the index also serves "every row
about runs" (`entity_type` alone).

### `ix_audit_log_occurred_at`: the newest audit rows, and time ranges

`GET /api/audit` (newest first, the default view) and `?from=` / `?to=`.

```
newest, without (17.6 ms):   Parallel Seq Scan on audit_log (495k rows) + top-N heapsort
newest, with    (0.018 ms):  Index Scan Backward using ix_audit_log_occurred_at (rows=52)
last 24 h, without (11.2 ms): Parallel Seq Scan  Rows Removed by Filter: 163172 (x3)
last 24 h, with    (0.027 ms): Index Scan Backward  Index Cond: occurred_at >= ...
last 24 h total, with (0.39 ms): Index Only Scan (5,554 rows)
```

The audit log is the biggest table and only grows. `id DESC` as a tie-breaker is handled by
an Incremental Sort on top of the index, so it needs no second column.

### `ix_leads_first_seen_at`: the lead list's default sort

`GET /api/leads` (newest first).

```
without (6.64 ms):  Parallel Seq Scan on leads + top-N heapsort
with    (0.027 ms): Index Scan Backward using ix_leads_first_seen_at (rows=51)
```

It also serves most filtered lists: with a common filter (e.g. `status=approved`), Postgres
walks this index from the newest lead and keeps the matching ones until the page is full.

### `ix_leads_score` (DESC NULLS LAST): best leads first

`GET /api/leads?sort=-score`: `ORDER BY score DESC NULLS LAST, id`.

```
without (6.80 ms):  Parallel Seq Scan on leads + top-N heapsort
with    (0.18 ms):  Index Scan using ix_leads_score + Incremental Sort (ties on score)
```

A plain `(score)` index read backwards gives `DESC NULLS FIRST`: the unscored leads would
come first, so it can't serve this sort. The index is declared `DESC NULLS LAST` to match.
SQLite can't build that, so it's Postgres-only (`ddl_if` in the model, a dialect check in
the migration).

### `ix_leads_status`: the rare statuses

`GET /api/leads?status=in_review` (or `escalated`, `new`).

```
in_review, without (5.93 ms):  Seq Scan on leads  Filter: status = 'in_review'  Rows Removed by Filter: 49916
in_review, with    (0.036 ms): Index Scan using ix_leads_status (rows=84)
approved, either way (0.06 ms): Index Scan Backward using ix_leads_first_seen_at  Filter: status = 'approved'
```

Kept for the rare statuses, which are the ones a reviewer filters for ("what's waiting",
"what was escalated"). For a common status Postgres rightly ignores it and uses
`ix_leads_first_seen_at`.

### `ix_leads_name_trgm` (GIN, pg_trgm): search inside the name

`GET /api/leads?q=harris`: `name ILIKE '%harris%'`. A normal B-tree can't help with a
leading `%`; a trigram index can.

```
page,  without (1.91 ms):  Index Scan Backward using ix_leads_first_seen_at  Filter: name ~~* '%harris%'  Rows Removed by Filter: 3733
page,  with    (0.65 ms):  Bitmap Index Scan on ix_leads_name_trgm (694 rows) → Bitmap Heap Scan
total, without (19.2 ms):  Seq Scan on leads  Rows Removed by Filter: 49307
total, with    (0.62 ms):  Bitmap Index Scan on ix_leads_name_trgm
```

The first page alone looks fine without it, because "harris" is common and turns up
quickly. The total every list answer needs (`paginate()`'s count) must read every name,
and a rare search term would make the page slow too. On SQLite it's a plain index.

### `ix_leads_source_ref` + `ix_leads_phone` + `ix_leads_website_domain`: the duplicate check

The `check` node, once per lead: `WHERE id <> :id AND NOT opt_out AND ((source_ref = :ref AND source <> :source) OR phone IN (...) OR website_domain = :domain)`.

```
without (7.66 ms):  Seq Scan on leads  Rows Removed by Filter: 50000
with    (0.011 ms): BitmapOr of Bitmap Index Scans on ix_leads_source_ref, ix_leads_phone, ix_leads_website_domain
```

An OR across three columns can only use indexes (a BitmapOr) when **each** of the three has
one; with any one missing, it's a full scan. This runs for every lead of every run, so it's
the query with the most calls after the worker's claim.

## Measured and left out

### `run_items(run_id)`: already covered

`GET /api/runs/{id}`: `WHERE run_id = :id ORDER BY id`.

```
without (0.030 ms): Index Scan using uq_run_items_run_id_lead_id  Index Cond: run_id = 1093
with    (0.033 ms): Index Scan using ix_run_items_run_id          Index Cond: run_id = 1093
```

The unique constraint `(run_id, lead_id)` already has an index that starts with `run_id`.
A second one would only cost space and slow down inserts.

### `runs(campaign_id)`: the table is too small

`GET /api/leads?campaign_id=`: `EXISTS (SELECT ... FROM run_items JOIN runs ... WHERE run_items.lead_id = leads.id AND runs.campaign_id = :id)`.

```
without (2.28 ms): Index Scan Backward using ix_leads_first_seen_at → Nested Loop Semi Join → Index Scan using ix_run_items_lead_id
with    (1.77 ms): the same plan
```

The plan is the same either way: Postgres walks the newest leads and checks each one's run
items through `ix_run_items_lead_id`. `runs` has 2,184 rows for 50k leads (one run finds
many leads), so a scan of it is cheap; the 0.5 ms difference is within the noise.

### `leads(area)`, `run_items.flags`: no index on purpose

- `?area=` is `lower(area) = :area`, and there are only 6 areas per city: each matches about
  a sixth of the leads, so a scan of `ix_leads_first_seen_at` with a filter is as good as an
  index.
- `?flag=` matches the JSON flags as text (`flags::text LIKE '%"possible_duplicate:%'`, the
  same on SQLite and Postgres). A GIN index on JSONB couldn't serve a `LIKE` on the text, and
  it would need a Postgres-only query. Only about 1 in 10 items has a flag; revisit if the
  flag filter gets slow.

## Reports (`GET /api/reports/{name}`)

Hand-written SQL in `backend/app/reporting/queries.py`, tested on Postgres against a
hand-built world with exact numbers (`tests/integration/test_reports_postgres.py`).

| report | what it shows | SQL features | time on the bench data |
|---|---|---|---|
| `daily_funnel` | per UTC day: collected, approved, rejected, escalated | `generate_series` (a row for every day), `count(*) FILTER`, JSONB `->>` | 37.8 ms |
| `approved_running_total` | per day: approved, and the running total | `sum() OVER (ORDER BY day)` + the total before the window | 5.6 ms |
| `top_leads_per_area` | best leads per city + area | `RANK() OVER (PARTITION BY city, area ...)` | 37.7 ms |
| `reviewer_stats` | per reviewer: decisions, approval %, median wait | `percentile_cont(0.5) WITHIN GROUP`, `FILTER` | 77–79 ms |
| `node_durations` | per node: avg and p95 time | `avg() FILTER`, `percentile_cont(0.95)`, `array_position` for pipeline order | 147 ms |
| `score_distribution` | leads per score bucket | `CASE` buckets, a `VALUES` list so empty buckets show 0 | 19.3 ms |
| `campaign_conversion` | per campaign: collected → approved, % with email, % duplicates | `bool_or`, `jsonb_array_elements_text`, `nullif` against ÷0 | 113 ms |

**No index helps them, and that's expected.** A report summarizes a big part of a table,
and reading most of a table is what a sequential scan is best at:

- `daily_funnel` (30 of the 90 days): with `ix_audit_log_occurred_at` Postgres can use the
  index for the date range, but it still reads 161k rows and the time is the same (37.8 ms
  either way). An index on `(action, occurred_at)` was not added: the report's 4 actions
  are 48k of the window's 161k rows (about 30%), still too many for an index to win.
- `reviewer_stats` first looked up each approval's gate step with a `LATERAL` subquery. With
  `ix_run_steps_run_item_id_started_at` that's fine, but **without it the query ran for
  minutes**: a scan of 320k steps for each of 49k approvals. It now groups the gate steps
  once and hash-joins them (78.8 ms without the index, 77.2 ms with it), so it doesn't
  depend on an index at all.
- `node_durations` and `campaign_conversion` sort ~320k / 50k rows and spill a few MB to
  disk (`external merge`) at Postgres's default `work_mem` (4 MB). Fine for a dashboard card;
  if they ever matter, raise `work_mem` for that query or cache the answer.

## Known costs

- **Every list answer counts its rows** (`paginate()` → `SELECT count(*) FROM (...)`). With a
  selective filter the count uses the same index (e.g. the last 24 h of audit rows: 0.39 ms).
  Without a filter it reads the whole table: the full audit log's total is a scan of 495k
  rows. That's tens of milliseconds today. At millions of rows the fix would be an
  estimated total (`pg_class.reltuples`) or "next page" links instead of a total.
- Every index slows down inserts a little. The busiest writer is the audit log (about 10 rows
  per lead), which now has 3 indexes besides its primary key. Loading 50k leads with their
  whole history (~1M rows) took 59 s without the new indexes and 57 s with them: building
  the rows in Python is the slow part, and the extra index work doesn't show at this size.
