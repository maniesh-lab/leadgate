/**
 * One run: its status, what collecting found, how many leads are in each state, and
 * the list of leads (run items). While the run is still moving, the page asks the API
 * again every few seconds, so a freshly started run fills in on its own; while it waits
 * for decisions, every 15 seconds, so decisions made elsewhere show up. Admins can delete
 * a run that has stopped moving.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import type { RunDetail, RunStatus } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { FlagBadge, ScoreMeter, StatusBadge } from "../components/Badge";
import { DeleteButton } from "../components/DeleteButton";
import { ExportButton } from "../components/ExportButton";
import { EmptyState, Fact, PageHeader, Stat } from "../components/Page";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

// A run in these states isn't moving on its own: the worker is done with it for now.
const SETTLED: RunStatus[] = ["completed", "failed", "waiting_approval"];
const REFRESH_MS = 3000;
// A run waiting for decisions moves on when someone decides in the queue (maybe in another
// tab, or someone else), so the page still checks, just less often.
const WAITING_REFRESH_MS = 15000;

// What collecting found, in the order people read it (from the run's `stats`).
const STAT_LABELS: [key: string, label: string][] = [
  ["collected", "Collected"],
  ["new", "New"],
  ["updated", "Updated"],
  ["duplicates", "Duplicates"],
  ["opted_out", "Opted out"],
  ["bad_rows", "Bad rows"],
  ["run_items", "In this run"],
];

export function RunPage() {
  const { runId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const run = useQuery({
    queryKey: ["runs", Number(runId)],
    queryFn: () => api.get<RunDetail>(`/api/runs/${runId}`),
    refetchInterval: (query) => refreshEvery(query.state.data?.status),
  });

  if (run.isPending) return <Loading />;
  if (run.error) return <ErrorMessage error={run.error} />;
  const data = run.data;
  const stats = data.stats as Record<string, unknown>;
  const failedSources = Object.entries((stats.failed_sources ?? {}) as Record<string, string>);
  const skippedSources = Object.entries((stats.skipped_sources ?? {}) as Record<string, string>);
  const moving = !SETTLED.includes(data.status);

  return (
    <section className="space-y-6">
      <PageHeader
        tone="green"
        eyebrow={`Run · ${data.trigger}`}
        title={
          <>
            Run <span className="text-gradient">#{data.id}</span>
          </>
        }
        subtitle={
          <>
            <Link to={`/leads?campaign_id=${data.campaign_id}`} className="link">
              {data.campaign_name}
            </Link>
            {moving && <span className="ml-2 text-faint">· updating every few seconds</span>}
          </>
        }
        actions={
          <>
            <StatusBadge status={data.status} />
            <Link to={`/audit?run_id=${data.id}`} className="btn px-3 py-1.5">
              Audit trail
            </Link>
            {user?.role === "admin" && (
              <>
                <ExportButton
                  target={{ run_id: data.id }}
                  filename={`leadgate-run-${data.id}-approved.csv`}
                />
                {!moving && (
                  <DeleteButton
                    path={`/api/runs/${data.id}`}
                    question={`Delete run #${data.id}? This can't be undone. Its leads go too, unless another run also found them. The audit log keeps its rows with those leads' names and contact details blanked. Leads already sent to Airtable or Discord stay there.`}
                    onDeleted={() => navigate(`/runs?campaign_id=${data.campaign_id}`)}
                  />
                )}
              </>
            )}
          </>
        }
      />

      {STAT_LABELS.some(([key]) => key in stats) && (
        <div className="card grid grid-cols-3 gap-5 sm:grid-cols-4 lg:grid-cols-7">
          {STAT_LABELS.filter(([key]) => key in stats).map(([key, label]) => (
            <Stat key={key} label={label} value={String(stats[key])} />
          ))}
        </div>
      )}

      <div className="card grid gap-4 sm:grid-cols-4">
        <Fact label="Created" value={formatDateTime(data.created_at)} />
        <Fact label="Started" value={formatDateTime(data.started_at)} />
        <Fact label="Finished" value={formatDateTime(data.finished_at)} />
        <Fact
          label="Correlation id"
          value={
            data.correlation_id && <span className="font-mono text-xs">{data.correlation_id}</span>
          }
        />
      </div>

      {failedSources.map(([source, error]) => (
        <ErrorMessage key={source} error={new Error(`Source ${source} failed: ${error}`)} />
      ))}
      {typeof stats.error === "string" && <ErrorMessage error={new Error(stats.error)} />}
      {skippedSources.map(([source, reason]) => (
        <p key={source} role="status" className="card text-sm text-(--amber)">
          Source {source} was skipped: {reason}
        </p>
      ))}

      <div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2 className="mr-2 text-lg font-semibold">Leads</h2>
          {Object.entries(data.counts).map(([status, count]) => (
            <span key={status} className="flex items-center gap-1.5 text-sm">
              <StatusBadge status={status} />
              <span className="font-semibold tabular-nums">{count}</span>
            </span>
          ))}
        </div>

        {data.items.length === 0 ? (
          <EmptyState
            title="No leads yet."
            hint={moving ? "The sources are still collecting." : "This run found no leads."}
          />
        ) : (
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Route</th>
                  <th>Flags</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/leads/${item.lead_id}`} className="font-semibold hover:underline">
                        {item.lead_name}
                      </Link>
                      {item.error && <div className="mt-1 text-xs text-(--red)">{item.error}</div>}
                    </td>
                    <td>
                      <StatusBadge status={item.status} />
                    </td>
                    <td>
                      <ScoreMeter score={item.score} />
                    </td>
                    <td>
                      <StatusBadge status={item.route} />
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {item.flags.map((flag) => (
                          <FlagBadge key={flag} flag={flag} />
                        ))}
                        {item.extraction_ok === false && <FlagBadge flag="extraction_failed" />}
                      </div>
                    </td>
                    <td className="text-right">
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
      </div>
    </section>
  );
}

/** How often to ask again: often while the run moves, now and then while it waits for
 * decisions, never once it's finished. */
export function refreshEvery(status: RunStatus | undefined): number | false {
  if (status === "waiting_approval") return WAITING_REFRESH_MS;
  return status && SETTLED.includes(status) ? false : REFRESH_MS;
}
