/**
 * Small coloured labels and the score meter. Statuses (lead, run, run item, step,
 * route, decision) get a dot + a colour by meaning, so "waiting for a human" is amber
 * on every page; flags are monospace chips; a score is a bold number with a bar.
 */
import type { ReactNode } from "react";

type Tone = "gray" | "blue" | "amber" | "green" | "red" | "violet";

// The colours come from the theme tokens in index.css (--green, --green-bg, ...).
const TONES: Record<Tone, string> = {
  gray: "bg-(--gray-bg) text-(--gray)",
  blue: "bg-(--blue-bg) text-(--blue)",
  amber: "bg-(--amber-bg) text-(--amber)",
  green: "bg-(--green-bg) text-(--green)",
  red: "bg-(--red-bg) text-(--red)",
  violet: "bg-(--violet-bg) text-(--violet)",
};

// Every status name the API uses (leads, runs, run items, steps, routes, decisions).
const STATUS_TONES: Record<string, Tone> = {
  new: "gray",
  queued: "gray",
  pending: "gray",
  skip: "gray",
  collecting: "blue",
  running: "blue",
  started: "blue",
  resuming: "blue",
  in_review: "amber",
  waiting_approval: "amber",
  awaiting_approval: "amber",
  approved: "green",
  completed: "green",
  done: "green",
  notify: "green",
  rejected: "red",
  failed: "red",
  escalated: "violet",
  escalate: "violet",
};

export function Badge({ tone = "gray", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

/** `awaiting_approval` -> an amber "• awaiting approval" badge. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  return (
    <Badge tone={STATUS_TONES[status] ?? "gray"}>
      <span className="size-1.5 rounded-full bg-current" />
      {status.replaceAll("_", " ")}
    </Badge>
  );
}

/** A flag from the check node, e.g. `possible_duplicate:12` -> "possible duplicate (12)". */
export function FlagBadge({ flag }: { flag: string }) {
  const [name, detail] = flag.split(":", 2);
  const label = name.replaceAll("_", " ") + (detail ? ` (${detail})` : "");
  const tone = name === "closed" ? "text-muted" : "text-(--amber)";
  return (
    <span
      className={`inline-flex rounded-full border border-line px-2 py-0.5 font-mono text-[11px] whitespace-nowrap ${tone}`}
    >
      {label}
    </span>
  );
}

/** A score out of 100: the number in bold and a short bar in the accent gradient. */
export function ScoreMeter({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) return <span className="text-faint">—</span>;
  return (
    <span className="inline-flex items-center gap-2" title={`score ${score} of 100`}>
      <span className="w-7 text-right font-semibold tabular-nums">{score}</span>
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-surface-2">
        <span
          className="block h-full rounded-full bg-linear-to-r from-accent to-accent-2"
          style={{ width: `${Math.max(score, 3)}%` }}
        />
      </span>
    </span>
  );
}
