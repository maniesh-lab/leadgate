/**
 * One run: its status, what collecting found, how many leads are in each state, and
 * the list of leads (run items). While the run is still moving, the page asks the API
 * again every few seconds, so a freshly started run fills in on its own.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import type { RunDetail, RunStatus } from "../api/types";
import { FlagBadge, StatusBadge } from "../components/Badge";
import { Fact } from "../components/Fact";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

// A run in these states won't change any more (until a human decides, for the queue).
const SETTLED: RunStatus[] = ["completed", "failed", "waiting_approval"];
const REFRESH_MS = 3000;

// What collecting found, in the order people read it (from the run's `stats`).
const STAT_LABELS: [key: string, label: string][] = [
  ["collected", "Collected"],
  ["new", "New"],
  ["updated", "Updated"],
  ["duplicates", "Duplicates"],
  ["opted_out", "Opted out"],
  ["bad_rows", "Bad rows"],
  ["run_items", "Leads in this run"],
];

export function RunPage() {
  const { runId } = useParams();
  const run = useQuery({
    queryKey: ["runs", Number(runId)],
    queryFn: () => api.get<RunDetail>(`/api/runs/${runId}`),
    refetchInterval: (query) =>
      query.state.data && SETTLED.includes(query.state.data.status) ? false : REFRESH_MS,
  });

  if (run.isPending) return <Loading />;
  if (run.error) return <ErrorMessage error={run.error} />;
  const data = run.data;
  const stats = data.stats as Record<string, unknown>;
  const failedSources = Object.entries((stats.failed_sources ?? {}) as Record<string, string>);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Run #{data.id}</h1>
        <StatusBadge status={data.status} />
        <Link to={`/leads?campaign_id=${data.campaign_id}`} className="link text-sm">
          {data.campaign_name}
        </Link>
      </div>

      <div className="card grid gap-3 text-sm sm:grid-cols-4">
        <Fact label="Trigger" value={data.trigger} />
        <Fact label="Created" value={formatDateTime(data.created_at)} />
        <Fact label="Started" value={formatDateTime(data.started_at)} />
        <Fact label="Finished" value={formatDateTime(data.finished_at)} />
        {STAT_LABELS.filter(([key]) => key in stats).map(([key, label]) => (
          <Fact key={key} label={label} value={String(stats[key])} />
        ))}
        <Fact label="Correlation id" value={data.correlation_id ?? "—"} />
      </div>

      {failedSources.length > 0 && (
        <div role="alert" className="card border-red-200 text-sm text-red-700">
          {failedSources.map(([source, error]) => (
            <p key={source}>
              Source {source} failed: {error}
            </p>
          ))}
        </div>
      )}
      {typeof stats.error === "string" && (
        <p role="alert" className="card border-red-200 text-sm text-red-700">
          {stats.error}
        </p>
      )}

      {Object.keys(data.counts).length > 0 && (
        <div className="flex flex-wrap gap-3 text-sm">
          {Object.entries(data.counts).map(([status, count]) => (
            <span key={status} className="flex items-center gap-1">
              <StatusBadge status={status} /> {count}
            </span>
          ))}
        </div>
      )}

      {data.items.length === 0 ? (
        <p className="text-sm text-slate-500">No leads yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Lead</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Score</th>
                <th className="px-3 py-2 font-medium">Route</th>
                <th className="px-3 py-2 font-medium">Flags</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id} className="border-b border-slate-100 align-top last:border-0">
                  <td className="px-3 py-2">
                    <Link to={`/leads/${item.lead_id}`} className="link">
                      {item.lead_name}
                    </Link>
                    {item.error && <div className="text-xs text-red-700">{item.error}</div>}
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={item.status} />
                  </td>
                  <td className="px-3 py-2">{item.score ?? "—"}</td>
                  <td className="px-3 py-2">
                    <StatusBadge status={item.route} />
                  </td>
                  <td className="space-x-1 px-3 py-2">
                    {item.flags.map((flag) => (
                      <FlagBadge key={flag} flag={flag} />
                    ))}
                    {item.extraction_ok === false && <FlagBadge flag="extraction_failed" />}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Link to={`/run-items/${item.id}`} className="link whitespace-nowrap">
                      Steps
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
