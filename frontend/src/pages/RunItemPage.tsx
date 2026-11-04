/**
 * One lead in one run: the lead card, the score breakdown, the human's decision, and the
 * step timeline: every node the agents ran (enrich -> extract -> check -> score ->
 * approval gate -> route -> act), how long it took, and what it produced.
 *
 * A failed lead gets an admin "Retry" button (`POST /api/run-items/{id}/retry`): the
 * worker continues it from where it stopped. While it is queued or running again, the
 * page asks the API every few seconds, so the timeline fills in on its own.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api, describeError } from "../api/client";
import type { RunItemDetail, RunItemRow, RunItemStatus, Step } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { FlagBadge, StatusBadge } from "../components/Badge";
import { GoogleCredit } from "../components/GoogleCredit";
import { RetryIcon } from "../components/icons";
import { EmptyState, Fact, PageHeader } from "../components/Page";
import { ErrorMessage, Loading } from "../components/QueryState";
import { ScoreReview } from "../components/ScoreReview";
import { formatDateTime, formatDuration } from "../lib/format";

// The timeline dot's colour by step status.
const DOTS: Record<string, string> = {
  completed: "bg-(--green)",
  failed: "bg-(--red)",
  started: "bg-accent animate-pulse",
};

// The worker is on it: ask again until the item reaches the gate, the end or fails.
const MOVING: RunItemStatus[] = ["pending", "running", "resuming"];
const REFRESH_MS = 3000;

export function RunItemPage() {
  const { itemId } = useParams();
  const { user } = useAuth();
  const item = useQuery({
    queryKey: ["run-items", Number(itemId)],
    queryFn: () => api.get<RunItemDetail>(`/api/run-items/${itemId}`),
    refetchInterval: (query) =>
      query.state.data && MOVING.includes(query.state.data.status) ? REFRESH_MS : false,
  });

  if (item.isPending) return <Loading />;
  if (item.error) return <ErrorMessage error={item.error} />;
  const data = item.data;
  const lead = data.lead;

  return (
    <section className="space-y-6">
      <PageHeader
        eyebrow={`Run #${data.run_id} · item ${data.id}`}
        title={
          <Link to={`/leads/${lead.id}`} className="hover:underline">
            {lead.name}
          </Link>
        }
        subtitle={
          <Link to={`/runs/${data.run_id}`} className="link">
            Back to run #{data.run_id}
          </Link>
        }
        actions={
          <>
            <StatusBadge status={data.status} />
            <StatusBadge status={data.route} />
            {data.status === "failed" && user?.role === "admin" && <RetryButton itemId={data.id} />}
          </>
        }
      />

      {(data.flags.length > 0 || data.extraction_ok === false) && (
        <div className="flex flex-wrap gap-1.5">
          {data.flags.map((flag) => (
            <FlagBadge key={flag} flag={flag} />
          ))}
          {data.extraction_ok === false && <FlagBadge flag="extraction_failed" />}
        </div>
      )}
      {data.error && <ErrorMessage error={new Error(data.error)} />}

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="card grid content-start gap-5 sm:grid-cols-2">
          <Fact label="Phone" value={lead.phone} />
          <Fact label="Email" value={lead.email} />
          <Fact label="Website" value={lead.website} />
          <Fact label="Category" value={lead.category} />
          <Fact label="Address" value={lead.address} />
          <Fact label="Area / city" value={[lead.area, lead.city].filter(Boolean).join(", ")} />
          <Fact
            label="Thread"
            value={<span className="font-mono text-xs">{data.thread_id}</span>}
          />
          <Fact label="Notified" value={data.notified_at && formatDateTime(data.notified_at)} />
          <GoogleCredit source={lead.source} />
        </div>
        <ScoreReview review={data.review} score={data.score} />
      </div>

      {data.approval && (
        <div className="card flex flex-wrap items-center gap-3 text-sm">
          <span className="eyebrow">Decision</span>
          <StatusBadge status={data.approval.decision} />
          <span className="font-medium">{data.approval.reviewer_email}</span>
          <span className="text-muted">{formatDateTime(data.approval.created_at)}</span>
          {data.approval.reason && <p className="w-full text-muted">“{data.approval.reason}”</p>}
        </div>
      )}

      <div>
        <h2 className="mb-3 text-lg font-semibold">Steps</h2>
        {data.steps.length === 0 ? (
          <EmptyState title="No steps yet." hint="The worker hasn't picked this lead up." />
        ) : (
          <ol className="card relative space-y-1 p-3">
            {data.steps.map((step) => (
              <StepRow key={step.id} step={step} />
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

/** One node run: name, status, time; click to see what it produced. */
function StepRow({ step }: { step: Step }) {
  return (
    <li className="rounded-xl transition-colors hover:bg-surface-2/60">
      <details>
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
          <span className={`size-2 shrink-0 rounded-full ${DOTS[step.status] ?? "bg-faint"}`} />
          <span className="w-32 font-mono font-medium">{step.node}</span>
          <StatusBadge status={step.status} />
          <span className="text-muted">{formatDateTime(step.started_at)}</span>
          <span className="ml-auto font-mono text-xs text-muted tabular-nums">
            {formatDuration(step.duration_ms)}
          </span>
        </summary>
        <div className="px-3 pb-3">
          {step.error && <p className="mb-2 text-sm text-(--red)">{step.error}</p>}
          {step.output ? (
            <pre className="max-h-80 overflow-auto rounded-xl border border-line bg-bg p-3 font-mono text-xs text-muted">
              {JSON.stringify(step.output, null, 2)}
            </pre>
          ) : (
            <p className="text-xs text-faint">No output.</p>
          )}
        </div>
      </details>
    </li>
  );
}

/** Queues a failed lead again; the page then refreshes until the worker is done with it. */
function RetryButton({ itemId }: { itemId: number }) {
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.post<RunItemRow>(`/api/run-items/${itemId}/retry`),
    onSettled: () => {
      // Also after a 409 (someone else retried it first): show where it is now.
      queryClient.invalidateQueries({ queryKey: ["run-items", itemId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  return (
    <div>
      <button
        type="button"
        className="btn px-3 py-1.5"
        title="Run this lead again from where it stopped"
        disabled={retry.isPending}
        onClick={() => retry.mutate()}
      >
        <RetryIcon className="size-3.5" />
        {retry.isPending ? "Retrying…" : "Retry"}
      </button>
      {retry.error && (
        <p role="alert" className="mt-1 text-xs text-(--red)">
          {describeError(retry.error)}
        </p>
      )}
    </div>
  );
}
