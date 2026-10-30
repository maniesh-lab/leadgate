/**
 * The campaign list: what each campaign looks for, where, with which sources and rubric.
 * Admins can start a run from here (it opens the new run's page). `?archived=true`
 * lists the archived campaigns instead.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api, describeError } from "../api/client";
import { rubricLabel, useRubrics } from "../api/hooks";
import type { Campaign, Page, Run } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Pagination } from "../components/Pagination";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;

export function CampaignsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const archived = params.get("archived") === "true";

  const campaigns = useQuery({
    queryKey: ["campaigns", { page, archived }],
    queryFn: () =>
      api.get<Page<Campaign>>("/api/campaigns", { page, page_size: PAGE_SIZE, archived }),
  });
  const rubrics = useRubrics();
  const rubricName = (id: number) => {
    const rubric = rubrics.data?.find((r) => r.id === id);
    return rubric ? rubricLabel(rubric) : `#${id}`;
  };

  function show(next: { page?: number; archived?: boolean }) {
    const query = new URLSearchParams();
    if (next.archived ?? archived) query.set("archived", "true");
    if (next.page && next.page > 1) query.set("page", String(next.page));
    setParams(query);
  }

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{archived ? "Archived campaigns" : "Campaigns"}</h1>
        <button
          type="button"
          className="link text-sm"
          onClick={() => show({ archived: !archived })}
        >
          {archived ? "Show open campaigns" : "Show archived"}
        </button>
        {isAdmin && (
          <Link to="/campaigns/new" className="btn-primary ml-auto">
            New campaign
          </Link>
        )}
      </div>

      {campaigns.isPending && <Loading />}
      {campaigns.error && <ErrorMessage error={campaigns.error} />}
      {campaigns.data && campaigns.data.items.length === 0 && (
        <p className="text-sm text-slate-500">No campaigns yet.</p>
      )}
      {campaigns.data && campaigns.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Campaign</th>
                  <th className="px-3 py-2 font-medium">Looks for</th>
                  <th className="px-3 py-2 font-medium">Sources</th>
                  <th className="px-3 py-2 font-medium">Rubric</th>
                  <th className="px-3 py-2 font-medium">Created</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {campaigns.data.items.map((campaign) => (
                  <tr key={campaign.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2">
                      <div className="font-medium">{campaign.name}</div>
                      <div className="space-x-3 text-xs">
                        <Link to={`/runs?campaign_id=${campaign.id}`} className="link">
                          Runs
                        </Link>
                        <Link to={`/leads?campaign_id=${campaign.id}`} className="link">
                          Leads
                        </Link>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {campaign.niche} in {campaign.location}
                    </td>
                    <td className="px-3 py-2">{campaign.sources.join(", ")}</td>
                    <td className="px-3 py-2">{rubricName(campaign.rubric_id)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatDateTime(campaign.created_at)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {isAdmin && !campaign.is_archived && <StartRunButton campaign={campaign} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={campaigns.data.total}
            onPage={(next) => show({ page: next })}
          />
        </>
      )}
    </section>
  );
}

/** Starts a run (the worker does the rest) and opens the run's page. */
function StartRunButton({ campaign }: { campaign: Campaign }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: () => api.post<Run>(`/api/campaigns/${campaign.id}/runs`),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  return (
    <div>
      <button
        type="button"
        className="btn"
        disabled={start.isPending}
        onClick={() => start.mutate()}
      >
        {start.isPending ? "Starting…" : "Start run"}
      </button>
      {start.error && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {describeError(start.error)}
        </p>
      )}
    </div>
  );
}
