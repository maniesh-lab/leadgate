/**
 * The run list, newest first, filtered by campaign and status. The filters live in the
 * URL (`/runs?campaign_id=3&status=completed`), so a filtered list can be shared or
 * bookmarked, and the campaign list links here.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import { useAllCampaigns } from "../api/hooks";
import type { Page, Run, RunStatus } from "../api/types";
import { StatusBadge } from "../components/Badge";
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
  const campaigns = useAllCampaigns();

  /** Change one filter (and go back to page 1), or the page. */
  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  }

  return (
    <section>
      <h1 className="mb-4 text-lg font-semibold">Runs</h1>
      <div className="mb-4 flex flex-wrap gap-3">
        <select
          aria-label="Campaign"
          value={campaignId}
          onChange={(e) => update("campaign_id", e.target.value)}
          className="input w-auto"
        >
          <option value="">All campaigns</option>
          {campaigns.data?.map((campaign) => (
            <option key={campaign.id} value={campaign.id}>
              {campaign.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => update("status", e.target.value)}
          className="input w-auto"
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
        <p className="text-sm text-slate-500">No runs match.</p>
      )}
      {runs.data && runs.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Run</th>
                  <th className="px-3 py-2 font-medium">Campaign</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Trigger</th>
                  <th className="px-3 py-2 font-medium">Leads</th>
                  <th className="px-3 py-2 font-medium">Started</th>
                  <th className="px-3 py-2 font-medium">Finished</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr key={run.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2">
                      <Link to={`/runs/${run.id}`} className="link">
                        #{run.id}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{run.campaign_name}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="px-3 py-2">{run.trigger}</td>
                    <td className="px-3 py-2">{String(run.stats.run_items ?? "—")}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatDateTime(run.started_at)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
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
