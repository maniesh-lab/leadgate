/**
 * The lead table with its filters. Every filter, the sort and the page live in the URL
 * (`/leads?status=approved&has_email=true&sort=-score`), so the browser's back button,
 * a reload and a shared link all show the same list. The search box waits until you
 * stop typing before it changes the URL.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { LeadRow, LeadStatus, Page } from "../api/types";
import { Badge, FlagBadge, ScoreMeter, StatusBadge } from "../components/Badge";
import { CampaignSelect } from "../components/CampaignSelect";
import { EmptyState, PageHeader } from "../components/Page";
import { Pagination } from "../components/Pagination";
import { ErrorMessage, Loading } from "../components/QueryState";

const PAGE_SIZE = 50;
const SEARCH_DELAY_MS = 300;

const STATUSES: LeadStatus[] = ["new", "in_review", "approved", "rejected", "escalated"];
const FLAGS = ["possible_duplicate", "category_mismatch", "closed", "rubric_gate"];
const SORTS: { value: string; label: string }[] = [
  { value: "-first_seen_at", label: "Newest first" },
  { value: "first_seen_at", label: "Oldest first" },
  { value: "-score", label: "Highest score" },
  { value: "score", label: "Lowest score" },
  { value: "name", label: "Name A–Z" },
  { value: "-name", label: "Name Z–A" },
  { value: "-updated_at", label: "Recently updated" },
];

/** The URL keys the API understands, sent as they are. */
export const FILTER_KEYS = [
  "status",
  "campaign_id",
  "area",
  "min_score",
  "max_score",
  "has_email",
  "flag",
  "q",
  "sort",
] as const;

export function LeadsPage() {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const filters = Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) ?? ""]));

  const leads = useQuery({
    queryKey: ["leads", { ...filters, page }],
    queryFn: () => api.get<Page<LeadRow>>("/api/leads", { ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: (previous) => previous, // keep the old rows while the next page loads
  });

  /** Change one filter (back to page 1), or the page. Empty = remove it from the URL. */
  function update(key: string, value: string) {
    // Build on the URL as it is now (the search box calls this after a pause).
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== "page") next.delete("page");
      return next;
    });
  }

  const hasFilters = FILTER_KEYS.some((key) => key !== "sort" && params.get(key));

  return (
    <section>
      <PageHeader
        tone="orange"
        eyebrow="Pipeline"
        title={<span className="text-gradient">Leads</span>}
        subtitle="Every business the sources found, cleaned and scored by the agents. Filter, sort, and open one to see why it scored what it did."
      />

      <div className="card mb-4 grid gap-3 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <SearchBox value={filters.q} onChange={(value) => update("q", value)} />
        <select
          aria-label="Status"
          value={filters.status}
          onChange={(e) => update("status", e.target.value)}
          className="input"
        >
          <option value="">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <CampaignSelect
          value={filters.campaign_id}
          onChange={(value) => update("campaign_id", value)}
        />
        <select
          aria-label="Flag"
          value={filters.flag}
          onChange={(e) => update("flag", e.target.value)}
          className="input"
        >
          <option value="">Any flags</option>
          {FLAGS.map((flag) => (
            <option key={flag} value={flag}>
              {flag.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <select
          aria-label="Email"
          value={filters.has_email}
          onChange={(e) => update("has_email", e.target.value)}
          className="input"
        >
          <option value="">With or without email</option>
          <option value="true">Email found</option>
          <option value="false">No email</option>
        </select>
        <input
          aria-label="Area"
          placeholder="Area (exact)"
          defaultValue={filters.area}
          key={filters.area}
          onBlur={(e) => update("area", e.target.value.trim())}
          onKeyDown={(e) => e.key === "Enter" && update("area", e.currentTarget.value.trim())}
          className="input"
        />
        <div className="flex items-center gap-2">
          <ScoreInput
            label="Min score"
            value={filters.min_score}
            onChange={(v) => update("min_score", v)}
          />
          <span className="text-faint">–</span>
          <ScoreInput
            label="Max score"
            value={filters.max_score}
            onChange={(v) => update("max_score", v)}
          />
        </div>
        <select
          aria-label="Sort"
          value={filters.sort || "-first_seen_at"}
          onChange={(e) =>
            update("sort", e.target.value === "-first_seen_at" ? "" : e.target.value)
          }
          className="input"
        >
          {SORTS.map((sort) => (
            <option key={sort.value} value={sort.value}>
              {sort.label}
            </option>
          ))}
        </select>
        {hasFilters && (
          <button type="button" className="link text-left text-sm" onClick={() => setParams({})}>
            Clear filters
          </button>
        )}
      </div>

      {leads.isPending && <Loading />}
      {leads.error && <ErrorMessage error={leads.error} />}
      {leads.data && leads.data.items.length === 0 && (
        <EmptyState
          title="No leads match."
          hint={hasFilters ? "Try fewer filters." : "Start a run from a campaign to collect some."}
        />
      )}
      {leads.data && leads.data.items.length > 0 && (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Contact</th>
                  <th>Area</th>
                  <th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {leads.data.items.map((lead) => (
                  <tr key={lead.id}>
                    <td className="min-w-48">
                      <Link to={`/leads/${lead.id}`} className="font-semibold hover:underline">
                        {lead.name}
                      </Link>
                      <div className="mt-0.5 font-mono text-[11px] text-faint">
                        {lead.category ?? lead.source}
                      </div>
                    </td>
                    <td>
                      <StatusBadge status={lead.status} />
                    </td>
                    <td>
                      <ScoreMeter score={lead.score} />
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {lead.email && <Badge tone="green">email</Badge>}
                        {lead.phone && <Badge>phone</Badge>}
                        {lead.website && <Badge>website</Badge>}
                        {lead.opt_out && <Badge tone="red">opted out</Badge>}
                      </div>
                    </td>
                    <td className="text-muted">{lead.area ?? lead.city ?? "—"}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {lead.flags.map((flag) => (
                          <FlagBadge key={flag} flag={flag} />
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={leads.data.total}
            onPage={(next) => update("page", String(next))}
          />
        </>
      )}
    </section>
  );
}

/** The name search: typing updates the box at once and the URL after a short pause. */
function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value);
  const sent = useRef(value); // the last search this box put in the URL

  // The URL changed elsewhere (back button, "Clear filters"): show that search.
  useEffect(() => {
    if (value !== sent.current) {
      sent.current = value;
      setText(value);
    }
  }, [value]);

  useEffect(() => {
    const search = text.trim();
    if (search === sent.current) return;
    const timer = setTimeout(() => {
      sent.current = search;
      onChange(search);
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, onChange]);

  return (
    <input
      type="search"
      aria-label="Search by name"
      placeholder="Search by name"
      value={text}
      onChange={(e) => setText(e.target.value)}
      className="input"
    />
  );
}

function ScoreInput(props: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <input
      type="number"
      min={0}
      max={100}
      aria-label={props.label}
      placeholder={props.label}
      defaultValue={props.value}
      key={props.value}
      onBlur={(e) => props.onChange(e.target.value)}
      onKeyDown={(e) => e.key === "Enter" && props.onChange(e.currentTarget.value)}
      className="input"
    />
  );
}
