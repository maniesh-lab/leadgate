/**
 * One lead in one run: the lead card, the score breakdown, the human's decision, and the
 * step timeline: every node the agents ran (enrich -> extract -> check -> score ->
 * approval gate -> route -> act), how long it took, and what it produced.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import type { RunItemDetail, Step } from "../api/types";
import { FlagBadge, StatusBadge } from "../components/Badge";
import { Fact } from "../components/Fact";
import { ErrorMessage, Loading } from "../components/QueryState";
import { ScoreReview } from "../components/ScoreReview";
import { formatDateTime, formatDuration } from "../lib/format";

export function RunItemPage() {
  const { itemId } = useParams();
  const item = useQuery({
    queryKey: ["run-items", Number(itemId)],
    queryFn: () => api.get<RunItemDetail>(`/api/run-items/${itemId}`),
  });

  if (item.isPending) return <Loading />;
  if (item.error) return <ErrorMessage error={item.error} />;
  const data = item.data;
  const lead = data.lead;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">
          <Link to={`/leads/${lead.id}`} className="link">
            {lead.name}
          </Link>
        </h1>
        <StatusBadge status={data.status} />
        <StatusBadge status={data.route} />
        <Link to={`/runs/${data.run_id}`} className="link text-sm">
          Run #{data.run_id}
        </Link>
      </div>

      {(data.flags.length > 0 || data.extraction_ok === false) && (
        <div className="flex flex-wrap gap-1">
          {data.flags.map((flag) => (
            <FlagBadge key={flag} flag={flag} />
          ))}
          {data.extraction_ok === false && <FlagBadge flag="extraction_failed" />}
        </div>
      )}
      {data.error && (
        <p role="alert" className="card border-red-200 text-sm text-red-700">
          {data.error}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="card grid grid-cols-2 gap-3 text-sm">
          <Fact label="Phone" value={lead.phone} />
          <Fact label="Email" value={lead.email} />
          <Fact label="Website" value={lead.website} />
          <Fact label="Category" value={lead.category} />
          <Fact label="Address" value={lead.address} />
          <Fact label="Area / city" value={[lead.area, lead.city].filter(Boolean).join(", ")} />
          <Fact label="Thread" value={data.thread_id} />
          <Fact label="Notified" value={formatDateTime(data.notified_at)} />
        </div>
        <ScoreReview review={data.review} score={data.score} />
      </div>

      {data.approval && (
        <div className="card text-sm">
          <StatusBadge status={data.approval.decision} /> by {data.approval.reviewer_email} on{" "}
          {formatDateTime(data.approval.created_at)}
          {data.approval.reason && <p className="mt-1 text-slate-600">“{data.approval.reason}”</p>}
        </div>
      )}

      <div>
        <h2 className="mb-2 font-medium">Steps</h2>
        {data.steps.length === 0 ? (
          <p className="text-sm text-slate-500">No steps yet.</p>
        ) : (
          <ol className="space-y-2">
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
    <li className="card py-2 text-sm">
      <details>
        <summary className="flex cursor-pointer flex-wrap items-center gap-3">
          <span className="w-32 font-mono">{step.node}</span>
          <StatusBadge status={step.status} />
          <span className="text-slate-500">{formatDateTime(step.started_at)}</span>
          <span className="ml-auto tabular-nums text-slate-500">
            {formatDuration(step.duration_ms)}
          </span>
        </summary>
        {step.error && <p className="mt-2 text-red-700">{step.error}</p>}
        {step.output && (
          <pre className="mt-2 max-h-80 overflow-auto rounded bg-slate-50 p-2 text-xs">
            {JSON.stringify(step.output, null, 2)}
          </pre>
        )}
      </details>
    </li>
  );
}
