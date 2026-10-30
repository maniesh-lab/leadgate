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
import { PlayIcon } from "../components/icons";
import { EmptyState, PageHeader } from "../components/Page";
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
      <PageHeader
        eyebrow={archived ? "Pipeline · archived" : "Pipeline"}
        title={<span className="text-gradient">Campaigns</span>}
        subtitle="What to look for, where, and which rubric scores it. Each run collects the leads again and sends them through the agents."
        actions={
          <button type="button" className="btn" onClick={() => show({ archived: !archived })}>
            {archived ? "Show open" : "Show archived"}
          </button>
        }
      />

      {campaigns.isPending && <Loading />}
      {campaigns.error && <ErrorMessage error={campaigns.error} />}
      {campaigns.data && campaigns.data.items.length === 0 && (
        <EmptyState
          title={archived ? "No archived campaigns." : "No campaigns yet."}
          hint={isAdmin ? "Create one: a niche, a city and a rubric." : "An admin creates them."}
        />
      )}
      {campaigns.data && campaigns.data.items.length > 0 && (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Campaign</th>
                  <th>Looks for</th>
                  <th>Sources</th>
                  <th>Rubric</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {campaigns.data.items.map((campaign) => (
                  <tr key={campaign.id}>
                    <td className="min-w-56">
                      <div className="font-semibold">{campaign.name}</div>
                      <div className="mt-1 flex gap-3 text-xs">
                        <Link to={`/runs?campaign_id=${campaign.id}`} className="link">
                          Runs
                        </Link>
                        <Link to={`/leads?campaign_id=${campaign.id}`} className="link">
                          Leads
                        </Link>
                      </div>
                    </td>
                    <td>
                      <span className="font-medium">{campaign.niche}</span>
                      <span className="text-muted"> in {campaign.location}</span>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {campaign.sources.map((source) => (
                          <span
                            key={source}
                            className="rounded-full border border-line px-2 py-0.5 font-mono text-[11px] text-muted"
                          >
                            {source}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="font-mono text-xs text-muted">
                      {rubricName(campaign.rubric_id)}
                    </td>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(campaign.created_at)}
                    </td>
                    <td className="text-right">
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
        className="btn px-3 py-1.5 whitespace-nowrap"
        disabled={start.isPending}
        onClick={() => start.mutate()}
      >
        <PlayIcon className="size-3.5 text-accent" />
        {start.isPending ? "Starting…" : "Start run"}
      </button>
      {start.error && (
        <p role="alert" className="mt-1 text-xs text-(--red)">
          {describeError(start.error)}
        </p>
      )}
    </div>
  );
}
