/**
 * The audit log: every step the agents, the worker and people took, newest first
 * (`GET /api/audit`). The rows never change: the API is read-only and Postgres refuses
 * updates and deletes. Filters live in the URL (`/audit?run_id=6&action=approval.approved`),
 * so the run page or a lead can link to its own trail. Dates are UTC days.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { ActorType, AuditRow, Page } from "../api/types";
import { Badge } from "../components/Badge";
import { EmptyState, PageHeader } from "../components/Page";
import { Pagination } from "../components/Pagination";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;
const ENTITY_TYPES = ["lead", "run", "run_item", "campaign", "rubric", "user"];
const ACTOR_TONES: Record<ActorType, "cyan" | "amber" | "gray"> = {
  agent: "cyan",
  user: "amber",
  system: "gray",
};

// Where an audit row's entity lives in the app, when it has a page.
const ENTITY_PAGES: Record<string, string> = {
  lead: "/leads",
  run: "/runs",
  run_item: "/run-items",
};

export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const filters = {
    run_id: params.get("run_id") ?? "",
    entity_type: params.get("entity_type") ?? "",
    entity_id: params.get("entity_id") ?? "",
    actor: params.get("actor") ?? "",
    action: params.get("action") ?? "",
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
  };

  const audit = useQuery({
    queryKey: ["audit", { page, ...filters }],
    queryFn: () =>
      api.get<Page<AuditRow>>("/api/audit", {
        page,
        page_size: PAGE_SIZE,
        ...filters,
        from: filters.from && `${filters.from}T00:00:00Z`,
        to: filters.to && `${nextDay(filters.to)}T00:00:00Z`, // "to" is the whole day
      }),
  });

  function update(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== "page") next.delete("page");
      return next;
    });
  }

  const filtered = Object.values(filters).some(Boolean);

  return (
    <section>
      <PageHeader
        eyebrow="Every step, on record"
        title={
          <>
            Audit <span className="text-gradient">log</span>
          </>
        }
        subtitle="What the agents, the worker and people did, newest first. Rows are append-only: nobody can change or delete them."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <TextFilter
          label="Action"
          placeholder="approval.approved"
          value={filters.action}
          onChange={(v) => update("action", v)}
        />
        <TextFilter
          label="Actor"
          placeholder="an email, a node, worker"
          value={filters.actor}
          onChange={(v) => update("actor", v)}
        />
        <TextFilter
          label="Run"
          placeholder="run id"
          value={filters.run_id}
          onChange={(v) => update("run_id", v)}
          numeric
        />
        <select
          aria-label="Entity"
          value={filters.entity_type}
          onChange={(e) => update("entity_type", e.target.value)}
          className="input"
        >
          <option value="">Any entity</option>
          {ENTITY_TYPES.map((type) => (
            <option key={type} value={type}>
              {type.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <TextFilter
          label="Entity id"
          placeholder="entity id"
          value={filters.entity_id}
          onChange={(v) => update("entity_id", v)}
          numeric
        />
        <DateFilter label="From (UTC)" value={filters.from} onChange={(v) => update("from", v)} />
        <DateFilter label="To (UTC)" value={filters.to} onChange={(v) => update("to", v)} />
        {filtered && (
          <button type="button" className="btn" onClick={() => setParams({})}>
            Clear filters
          </button>
        )}
      </div>

      {audit.isPending && <Loading />}
      {audit.error && <ErrorMessage error={audit.error} />}
      {audit.data && audit.data.items.length === 0 && (
        <EmptyState
          title="No audit rows match."
          hint={filtered ? "Loosen the filters." : undefined}
        />
      )}
      {audit.data && audit.data.items.length > 0 && (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Entity</th>
                  <th>Run</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {audit.data.items.map((row) => (
                  <tr key={row.id}>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(row.occurred_at)}
                    </td>
                    <td>
                      <div className="flex items-center gap-2">
                        <Badge tone={ACTOR_TONES[row.actor_type]}>{row.actor_type}</Badge>
                        <span className="text-xs break-all">{row.actor}</span>
                      </div>
                    </td>
                    <td className="font-mono text-xs font-medium whitespace-nowrap">
                      {row.action}
                    </td>
                    <td className="whitespace-nowrap">
                      <EntityLink type={row.entity_type} id={row.entity_id} />
                    </td>
                    <td>
                      {row.run_id ? (
                        <Link to={`/runs/${row.run_id}`} className="link font-mono text-xs">
                          #{row.run_id}
                        </Link>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </td>
                    <td className="min-w-56">
                      <Details details={row.details} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={audit.data.total}
            onPage={(next) => update("page", String(next))}
          />
        </>
      )}
    </section>
  );
}

/** "2026-10-30" -> "2026-10-31" (UTC), so a "to" day includes all of that day. */
function nextDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

function EntityLink({ type, id }: { type: string; id: number | null }) {
  const label = `${type.replaceAll("_", " ")}${id === null ? "" : ` #${id}`}`;
  const base = ENTITY_PAGES[type];
  if (!base || id === null) return <span className="text-xs text-muted">{label}</span>;
  return (
    <Link to={`${base}/${id}`} className="link text-xs">
      {label}
    </Link>
  );
}

/** The row's details JSON: a one-line preview, the whole thing on click. */
function Details({ details }: { details: Record<string, unknown> }) {
  const text = JSON.stringify(details);
  if (text === "{}") return <span className="text-faint">—</span>;
  return (
    <details>
      <summary className="max-w-80 cursor-pointer truncate font-mono text-xs text-muted">
        {text}
      </summary>
      <pre className="mt-2 max-h-64 overflow-auto rounded-xl border border-line bg-bg p-3 font-mono text-xs text-muted">
        {JSON.stringify(details, null, 2)}
      </pre>
    </details>
  );
}

/** A text box that filters on leaving it or on Enter (not on every key). */
function TextFilter(props: {
  label: string;
  placeholder: string;
  value: string;
  numeric?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <input
      aria-label={props.label}
      placeholder={props.placeholder}
      inputMode={props.numeric ? "numeric" : undefined}
      className="input"
      defaultValue={props.value}
      key={props.value}
      onBlur={(e) => props.onChange(e.target.value.trim())}
      onKeyDown={(e) => e.key === "Enter" && props.onChange(e.currentTarget.value.trim())}
    />
  );
}

function DateFilter(props: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="flex items-center gap-2">
      <span className="eyebrow shrink-0 text-[10px]">{props.label}</span>
      <input
        type="date"
        className="input"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </label>
  );
}
