/**
 * The run list, newest first, filtered by campaign and status. The filters live in the
 * URL (`/runs?campaign_id=3&status=completed`), so a filtered list can be shared or
 * bookmarked, and the campaign list links here.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Page, Run, RunStatus } from "../api/types";
import { StatusBadge } from "../components/Badge";
import { CampaignSelect } from "../components/CampaignSelect";
import { EmptyState, PageHeader } from "../components/Page";
import { Pagination } from "../components/Pagination";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;
const STATUSES: RunStatus[] = [
  "queued",
  "collecting",
  "running",
  "waiting_approval",
  "completed",
  "failed",
];

export function RunsPage() {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const campaignId = params.get("campaign_id") ?? "";
  const status = params.get("status") ?? "";

  const runs = useQuery({
    queryKey: ["runs", { page, campaignId, status }],
    queryFn: () =>
      api.get<Page<Run>>("/api/runs", {
        page,
        page_size: PAGE_SIZE,
        campaign_id: campaignId,
        status,
      }),
  });

  /** Change one filter (and go back to page 1), or the page. */
  function update(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== "page") next.delete("page");
      return next;
    });
  }

  return (
    <section>
      <PageHeader
        eyebrow="Activity"
        title={<span className="text-gradient">Runs</span>}
        subtitle="Each run collects a campaign's leads and takes every one through enrich, extract, check and score, up to a human's approval."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_220px]">
        <CampaignSelect value={campaignId} onChange={(value) => update("campaign_id", value)} />
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => update("status", e.target.value)}
          className="input"
        >
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </div>

      {runs.isPending && <Loading />}
      {runs.error && <ErrorMessage error={runs.error} />}
      {runs.data && runs.data.items.length === 0 && (
        <EmptyState
          title="No runs match."
          hint="Start one from a campaign, or loosen the filters."
          action={
            <Link to="/campaigns" className="btn">
              Go to campaigns
            </Link>
          }
        />
      )}
      {runs.data && runs.data.items.length > 0 && (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Campaign</th>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th className="text-right">Leads</th>
                  <th>Started</th>
                  <th>Finished</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link to={`/runs/${run.id}`} className="link font-mono font-semibold">
                        #{run.id}
                      </Link>
                    </td>
                    <td className="min-w-48 font-medium">{run.campaign_name}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="font-mono text-xs text-muted">{run.trigger}</td>
                    <td className="text-right font-semibold tabular-nums">
                      {String(run.stats.run_items ?? "—")}
                    </td>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(run.started_at)}
                    </td>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(run.finished_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={runs.data.total}
            onPage={(next) => update("page", String(next))}
          />
        </>
      )}
    </section>
  );
}
