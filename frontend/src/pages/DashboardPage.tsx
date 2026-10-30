/**
 * The dashboard (the start page): headline numbers, then one card per report from
 * `GET /api/reports/{name}` (Phase 9's hand-written SQL). The period (7, 30 or 90 days)
 * is in the URL (`/dashboard?days=7`) and applies to the daily reports; the others cover
 * everything. Each card loads on its own, so one slow or failed report doesn't blank
 * the page. Reports need Postgres (the API answers 501 on SQLite).
 */
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Page, QueueItem, Report, ReportName } from "../api/types";
import { BarChart } from "../components/BarChart";
import { PageHeader, Stat } from "../components/Page";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDuration } from "../lib/format";

const PERIODS = [7, 30, 90];

// The rows each report returns (column names from app/reporting/queries.py).
type FunnelDay = {
  day: string;
  collected: number;
  approved: number;
  rejected: number;
  escalated: number;
};
type TotalDay = { day: string; approved: number; running_total: number };
type Bucket = { bucket: string; leads: number; approved: number; rejected: number };
type ReviewerRow = {
  reviewer: string;
  decisions: number;
  approved: number;
  rejected: number;
  approved_pct: number | null;
  median_wait_minutes: number | null;
};
type NodeRow = {
  node: string;
  completed: number;
  failed: number;
  avg_ms: number | null;
  p95_ms: number | null;
};
type CampaignRow = {
  campaign_id: number;
  name: string;
  collected: number;
  approved: number;
  approved_pct: number | null;
  emails_found_pct: number | null;
  duplicates_pct: number | null;
};
type TopLead = {
  city: string | null;
  area: string | null;
  lead_id: number;
  name: string;
  score: number;
};

function useReport<Row>(name: ReportName, params: { days?: number; top?: number } = {}) {
  return useQuery({
    queryKey: ["reports", name, params],
    queryFn: () => api.get<Report>(`/api/reports/${name}`, params),
    select: (report) => report.rows as Row[],
  });
}

const DAY = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const dayLabel = (iso: string) => DAY.format(new Date(`${iso}T00:00:00Z`));
const pct = (value: number | null) => (value === null ? "—" : `${value}%`);
const sum = <Row,>(rows: Row[], pick: (row: Row) => number) =>
  rows.reduce((total, row) => total + pick(row), 0);

export function DashboardPage() {
  const [params, setParams] = useSearchParams();
  const days = PERIODS.includes(Number(params.get("days"))) ? Number(params.get("days")) : 30;

  const funnel = useReport<FunnelDay>("daily_funnel", { days });
  const total = useReport<TotalDay>("approved_running_total", { days });
  const waiting = useQuery({
    queryKey: ["approvals", "queue", "count"],
    queryFn: () => api.get<Page<QueueItem>>("/api/approvals/queue", { page_size: 1 }),
    select: (page) => page.total,
  });

  const rows = funnel.data ?? [];
  const approved = sum(rows, (r) => r.approved);
  const rejected = sum(rows, (r) => r.rejected);

  return (
    <section className="space-y-6">
      <PageHeader
        eyebrow="Overview"
        title={
          <>
            The <span className="text-gradient">pipeline</span>
          </>
        }
        subtitle="Leads found, checked and scored by the agents, and what people decided. Days are UTC."
        actions={
          <fieldset className="flex gap-1 rounded-full border border-line p-1" aria-label="Period">
            {PERIODS.map((period) => (
              <button
                key={period}
                type="button"
                aria-pressed={period === days}
                onClick={() => setParams(period === 30 ? {} : { days: String(period) })}
                className={`rounded-full px-3 py-1 font-mono text-xs transition-colors ${
                  period === days ? "bg-surface-2 text-fg" : "text-muted hover:text-fg"
                }`}
              >
                {period}d
              </button>
            ))}
          </fieldset>
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Link to="/queue" className="card transition-colors hover:border-line-strong">
          <Stat label="Waiting for review" value={waiting.data ?? "…"} />
        </Link>
        <div className="card">
          <Stat
            label={`Collected · ${days}d`}
            value={funnel.data ? sum(rows, (r) => r.collected) : "…"}
          />
        </div>
        <div className="card">
          <Stat label={`Approved · ${days}d`} value={funnel.data ? approved : "…"} />
          {approved + rejected > 0 && (
            <p className="mt-2 text-xs text-muted">
              {Math.round((100 * approved) / (approved + rejected))}% of decisions
            </p>
          )}
        </div>
        <div className="card">
          <Stat
            label={`Escalated · ${days}d`}
            value={funnel.data ? sum(rows, (r) => r.escalated) : "…"}
          />
        </div>
        <div className="card col-span-2 lg:col-span-1">
          <Stat label="Approved · all time" value={total.data?.at(-1)?.running_total ?? "…"} />
        </div>
      </div>
      {funnel.error && <ErrorMessage error={funnel.error} />}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Collected per day" note="New leads the sources found.">
          {funnel.data && (
            <BarChart
              summary={`${sum(rows, (r) => r.collected)} collected in ${days} days · hover a bar`}
              bars={rows.map((r) => ({
                label: dayLabel(r.day),
                value: r.collected,
                detail: `${r.collected} collected`,
              }))}
            />
          )}
        </Card>
        <Card title="Approved per day" note="Leads a reviewer sent on to the CRM.">
          {funnel.data && (
            <BarChart
              summary={`${approved} approved, ${rejected} rejected · hover a bar`}
              bars={rows.map((r) => ({
                label: dayLabel(r.day),
                value: r.approved,
                detail: `${r.approved} approved · ${r.rejected} rejected · ${r.escalated} escalated`,
              }))}
            />
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ScoreCard />
        <ReviewerCard />
      </div>
      <CampaignCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <NodeCard />
        <TopLeadsCard />
      </div>
    </section>
  );
}

function Card({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <div className="card min-w-0">
      <h2 className="font-semibold">{title}</h2>
      <p className="mb-4 text-xs text-muted">{note}</p>
      {children}
    </div>
  );
}

/** Loading / error / "no rows yet" for a report card; the rows otherwise. */
function ReportBody<Row>({
  query,
  children,
}: {
  query: { isPending: boolean; error: unknown; data?: Row[] };
  children: (rows: Row[]) => ReactNode;
}) {
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorMessage error={query.error} />;
  if (!query.data || query.data.length === 0)
    return <p className="text-sm text-faint">Nothing yet.</p>;
  return children(query.data);
}

function ScoreCard() {
  const buckets = useReport<Bucket>("score_distribution");
  return (
    <Card title="Score distribution" note="Every scored lead, by score.">
      <ReportBody query={buckets}>
        {(rows) => (
          <BarChart
            labelAll
            summary={`${sum(rows, (r) => r.leads)} scored leads · hover a bar`}
            bars={rows.map((r) => ({
              label: r.bucket,
              value: r.leads,
              detail: `${r.leads} leads · ${r.approved} approved · ${r.rejected} rejected`,
            }))}
          />
        )}
      </ReportBody>
    </Card>
  );
}

function ReviewerCard() {
  const reviewers = useReport<ReviewerRow>("reviewer_stats");
  return (
    <Card title="Reviewers" note="Decisions and the median wait from the gate to the decision.">
      <ReportBody query={reviewers}>
        {(rows) => (
          <Table head={["Reviewer", "Decisions", "Approved", "Median wait"]}>
            {rows.map((r) => (
              <tr key={r.reviewer}>
                <td className="max-w-48 truncate">{r.reviewer}</td>
                <Num>{r.decisions}</Num>
                <Num>{pct(r.approved_pct)}</Num>
                <Num>
                  {r.median_wait_minutes === null
                    ? "—"
                    : formatDuration(Math.round(r.median_wait_minutes * 60_000))}
                </Num>
              </tr>
            ))}
          </Table>
        )}
      </ReportBody>
    </Card>
  );
}

function CampaignCard() {
  const campaigns = useReport<CampaignRow>("campaign_conversion");
  return (
    <Card
      title="Campaigns"
      note="From collected to approved, and how many had an email or a duplicate."
    >
      <ReportBody query={campaigns}>
        {(rows) => (
          <Table
            head={["Campaign", "Collected", "Approved", "Approved %", "Email found", "Duplicates"]}
          >
            {rows.map((r) => (
              <tr key={r.campaign_id}>
                <td>
                  <Link to={`/runs?campaign_id=${r.campaign_id}`} className="link">
                    {r.name}
                  </Link>
                </td>
                <Num>{r.collected}</Num>
                <Num>{r.approved}</Num>
                <Num>{pct(r.approved_pct)}</Num>
                <Num>{pct(r.emails_found_pct)}</Num>
                <Num>{pct(r.duplicates_pct)}</Num>
              </tr>
            ))}
          </Table>
        )}
      </ReportBody>
    </Card>
  );
}

function NodeCard() {
  const nodes = useReport<NodeRow>("node_durations");
  return (
    <Card
      title="Agent steps"
      note="Time spent in each graph node (the human's wait isn't counted)."
    >
      <ReportBody query={nodes}>
        {(rows) => (
          <Table head={["Node", "Done", "Failed", "Average", "p95"]}>
            {rows.map((r) => (
              <tr key={r.node}>
                <td className="font-mono text-xs">{r.node}</td>
                <Num>{r.completed}</Num>
                <Num>{r.failed > 0 ? <span className="text-(--red)">{r.failed}</span> : 0}</Num>
                <Num>{formatDuration(r.avg_ms)}</Num>
                <Num>{formatDuration(r.p95_ms)}</Num>
              </tr>
            ))}
          </Table>
        )}
      </ReportBody>
    </Card>
  );
}

function TopLeadsCard() {
  const leads = useReport<TopLead>("top_leads_per_area", { top: 1 });
  return (
    <Card
      title="Best lead per area"
      note="The top-scored lead in each city and area (rejected ones left out)."
    >
      <ReportBody query={leads}>
        {(rows) => (
          <Table head={["Area", "Lead", "Score"]}>
            {rows.map((r) => (
              <tr key={r.lead_id}>
                <td className="text-muted">{[r.area, r.city].filter(Boolean).join(", ") || "—"}</td>
                <td>
                  <Link to={`/leads/${r.lead_id}`} className="link">
                    {r.name}
                  </Link>
                </td>
                <Num>{r.score}</Num>
              </tr>
            ))}
          </Table>
        )}
      </ReportBody>
    </Card>
  );
}

function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="-mx-5 max-h-80 overflow-auto">
      <table className="data-table">
        <thead>
          <tr>
            {head.map((label, index) => (
              <th key={label} className={index > 0 ? "text-right" : undefined}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Num({ children }: { children: ReactNode }) {
  return <td className="text-right tabular-nums whitespace-nowrap">{children}</td>;
}
