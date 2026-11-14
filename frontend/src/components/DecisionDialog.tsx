/**
 * The decision dialog of the approval queue: the lead card (key fields, flags, the
 * rubric that scored it, score breakdown + explanation, website signals) and the two buttons. A rejection needs a
 * reason; an approval may have one. Keys: A approves, R rejects, Esc closes (A and R
 * are ignored while typing the reason, and a held key's repeats are ignored).
 *
 * Focus moves into the dialog when it opens (and onto each next lead), Tab stays inside
 * it, and closing puts focus back where it was, so keyboard and screen reader users
 * aren't left behind the overlay.
 *
 * The decision is `POST /api/run-items/{id}/decision`. A 409 means the item isn't
 * waiting any more (someone else decided first): the dialog stays open and says so,
 * and the queue page reloads the list when it closes. After a decision the queue page
 * opens the next item (`onDecided`).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError, api, describeError } from "../api/client";
import type { Approval, Decision, LeadDetail, QueueItem } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { demoLogin } from "../lib/demo";
import { FlagBadge } from "./Badge";
import { type Enrichment, SignalList } from "./Enrichment";
import { GoogleCredit } from "./GoogleCredit";
import { Fact } from "./Page";
import { ScoreReview } from "./ScoreReview";

type Props = {
  item: QueueItem;
  onClose: () => void;
  onDecided: () => void;
};

export function DecisionDialog({ item, onClose, onDecided }: Props) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const open = useRef(true);
  useEffect(
    () => () => {
      open.current = false;
    },
    [],
  );
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const { user } = useAuth();
  // Every visitor shares the public demo's login, so the API keeps no reason typed there.
  const onDemoLogin = user !== null && user.email === demoLogin()?.email;
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
      // Closed while the decision was on its way: don't open the next lead.
      if (open.current) onDecided();
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

  // A fresh item: start with an empty reason and no old error, and focus on its card.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when the item changes
  useEffect(() => {
    setReason("");
    setProblem(null);
    decide.reset();
    dialogRef.current?.focus();
  }, [item.run_item_id]);

  // Closing gives focus back to what had it before the dialog opened (the Review button).
  // Read on the first render: by the time effects run, the dialog has taken focus.
  const [opener] = useState(() => document.activeElement);
  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus();
    },
    [opener],
  );

  // Keyboard shortcuts; `submit` is read fresh each render through the ref.
  const submitRef = useRef(submit);
  submitRef.current = submit;
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key === "Tab" && dialogRef.current) {
        keepTabInside(event, dialogRef.current);
        return;
      }
      const target = event.target as HTMLElement;
      const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(target.tagName);
      if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key !== "a" && key !== "r") return;
      event.preventDefault(); // so R doesn't type an "r" into the reason box it focuses
      // A held key repeats: each repeat would decide the next lead as soon as it opens,
      // before anyone has read it (and an approval goes to the CRM and Discord).
      if (event.repeat) return;
      submitRef.current(key === "a" ? "approved" : "rejected");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const enrichment = (detail.data?.enrichment ?? null) as Enrichment | null;

  return (
    <div className="fixed inset-0 z-20 flex items-start justify-center overflow-y-auto bg-bg/70 px-4 py-8 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="card w-full max-w-4xl space-y-5 shadow-2xl outline-none"
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
              <GoogleCredit source={lead.source} />
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
          {onDemoLogin && (
            <p className="mt-1 text-xs text-muted">
              Every visitor shares the demo login, so the reason you type isn't kept.
            </p>
          )}
        </div>

        {(problem || decide.error) && (
          <p role="alert" className="text-sm text-(--red)">
            {problem ?? decisionError(decide.error)}
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

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex="0"]';

/** Tab from the last control goes to the first (Shift+Tab the other way), and Tab from
 * outside the dialog comes back in: the page behind the overlay is out of reach. */
function keepTabInside(event: KeyboardEvent, dialog: HTMLElement) {
  const controls = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)];
  if (controls.length === 0) return;
  const first = controls[0];
  const last = controls[controls.length - 1];
  const active = document.activeElement;
  const inside = active instanceof Node && dialog.contains(active) && active !== dialog;
  if (!inside) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

/** A 409 gets a plain explanation; anything else the API's own message. */
function decisionError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return `Someone else got here first: ${error.message}. Close this to reload the queue.`;
  }
  return describeError(error);
}
