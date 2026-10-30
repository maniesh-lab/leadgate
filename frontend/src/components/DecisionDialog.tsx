/**
 * The decision dialog of the approval queue: the lead card (key fields, flags, score
 * breakdown + explanation, website signals) and the two buttons. A rejection needs a
 * reason; an approval may have one. Keys: A approves, R rejects, Esc closes (A and R
 * are ignored while typing the reason).
 *
 * The decision is `POST /api/run-items/{id}/decision`. A 409 means the item isn't
 * waiting any more (someone else decided first): the message says so and the queue
 * reloads. After a decision the queue page opens the next item (`onDecided`).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError, api, describeError } from "../api/client";
import type { Approval, Decision, LeadDetail, QueueItem } from "../api/types";
import { FlagBadge } from "./Badge";
import { type Enrichment, SignalList } from "./Enrichment";
import { Fact } from "./Page";
import { ScoreReview } from "./ScoreReview";

type Props = {
  item: QueueItem;
  onClose: () => void;
  onDecided: () => void;
};

export function DecisionDialog({ item, onClose, onDecided }: Props) {
  const titleId = useId();
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const lead = item.lead;

  // The queue item has no enrichment; the lead's own page data has the site signals.
  const detail = useQuery({
    queryKey: ["leads", lead.id],
    queryFn: () => api.get<LeadDetail>(`/api/leads/${lead.id}`),
  });

  const decide = useMutation({
    mutationFn: (decision: Decision) =>
      api.post<Approval>(`/api/run-items/${item.run_item_id}/decision`, {
        decision,
        reason: reason.trim() || null,
      }),
    onSuccess: () => {
      // Statuses change everywhere: the queue, the run, the lead.
      queryClient.invalidateQueries();
      onDecided();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        queryClient.invalidateQueries({ queryKey: ["approvals"] });
      }
    },
  });

  function submit(decision: Decision) {
    if (decide.isPending) return;
    if (decision === "approved" && lead.opt_out) {
      setProblem("This lead opted out: it can only be rejected.");
      return;
    }
    if (decision === "rejected" && !reason.trim()) {
      setProblem("Write a reason to reject this lead.");
      reasonRef.current?.focus();
      return;
    }
    setProblem(null);
    decide.mutate(decision);
  }

  // A fresh item: start with an empty reason and no old error.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when the item changes
  useEffect(() => {
    setReason("");
    setProblem(null);
    decide.reset();
  }, [item.run_item_id]);

  // Keyboard shortcuts; `submit` is read fresh each render through the ref.
  const submitRef = useRef(submit);
  submitRef.current = submit;
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      const target = event.target as HTMLElement;
      const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(target.tagName);
      if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "a") submitRef.current("approved");
      if (key === "r") submitRef.current("rejected");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const enrichment = (detail.data?.enrichment ?? null) as Enrichment | null;

  return (
    <div className="fixed inset-0 z-20 flex items-start justify-center overflow-y-auto bg-bg/70 px-4 py-8 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="card w-full max-w-4xl space-y-5 shadow-2xl"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="eyebrow mb-2">
              {item.campaign_name} · run #{item.run_id}
            </p>
            <h2 id={titleId} className="text-3xl font-semibold tracking-tight">
              {lead.name}
            </h2>
            <Link to={`/run-items/${item.run_item_id}`} className="link text-sm">
              See every step
            </Link>
          </div>
          <button type="button" className="btn px-3 py-1" onClick={onClose}>
            Close <kbd className="font-mono text-[10px] text-faint">Esc</kbd>
          </button>
        </div>

        {(item.flags.length > 0 || item.extraction_ok === false || lead.opt_out) && (
          <div className="flex flex-wrap gap-1.5">
            {item.flags.map((flag) => (
              <FlagBadge key={flag} flag={flag} />
            ))}
            {item.extraction_ok === false && <FlagBadge flag="extraction_failed" />}
            {lead.opt_out && <FlagBadge flag="opted_out" />}
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          <div className="space-y-4">
            <div className="grid gap-5 rounded-2xl border border-line p-4 sm:grid-cols-2">
              <Fact label="Phone" value={lead.phone} />
              <Fact label="Email" value={lead.email} />
              <Fact label="Website" value={lead.website} />
              <Fact label="Category" value={lead.category} />
              <Fact label="Address" value={lead.address} />
              <Fact
                label="Rating"
                value={lead.rating === null ? null : `${lead.rating} ★ (${lead.review_count ?? 0})`}
              />
            </div>
            <div className="rounded-2xl border border-line p-4 text-sm">
              <h3 className="eyebrow mb-3">Website signals</h3>
              {detail.isPending && <p className="text-muted">Loading…</p>}
              {detail.error && <p className="text-(--red)">{describeError(detail.error)}</p>}
              {detail.data &&
                (lead.website && enrichment ? (
                  <SignalList enrichment={enrichment} />
                ) : (
                  <p className="text-muted">
                    {lead.website ? "The website hasn't been visited." : "No website."}
                  </p>
                ))}
            </div>
          </div>
          <ScoreReview review={item.review} score={item.score} />
        </div>

        <div>
          <label htmlFor={`${titleId}-reason`} className="eyebrow mb-2 block">
            Reason <span className="normal-case tracking-normal">(needed to reject)</span>
          </label>
          <textarea
            id={`${titleId}-reason`}
            ref={reasonRef}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={1000}
            rows={2}
            className="input"
            placeholder="e.g. a chain, not a small practice"
          />
        </div>

        {(problem || decide.error) && (
          <p role="alert" className="text-sm text-(--red)">
            {problem ?? describeError(decide.error)}
          </p>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="btn hover:border-(--red)/50 hover:text-(--red)"
            disabled={decide.isPending}
            onClick={() => submit("rejected")}
          >
            Reject <kbd className="font-mono text-[10px] text-faint">R</kbd>
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={decide.isPending || lead.opt_out}
            title={lead.opt_out ? "This lead opted out: it can only be rejected." : undefined}
            onClick={() => submit("approved")}
          >
            Approve <kbd className="font-mono text-[10px] opacity-60">A</kbd>
          </button>
        </div>
      </div>
    </div>
  );
}
