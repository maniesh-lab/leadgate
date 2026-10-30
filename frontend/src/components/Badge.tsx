/**
 * Small coloured labels: statuses (lead, run, run item, step), routes and flags.
 * One colour per meaning, so "waiting for a human" looks the same on every page.
 */
import type { ReactNode } from "react";

type Tone = "gray" | "blue" | "amber" | "green" | "red" | "purple";

const TONES: Record<Tone, string> = {
  gray: "bg-slate-100 text-slate-700",
  blue: "bg-sky-100 text-sky-800",
  amber: "bg-amber-100 text-amber-800",
  green: "bg-emerald-100 text-emerald-800",
  red: "bg-red-100 text-red-800",
  purple: "bg-violet-100 text-violet-800",
};

// Every status name the API uses (leads, runs, run items, steps, routes, decisions).
const STATUS_TONES: Record<string, Tone> = {
  new: "gray",
  queued: "gray",
  pending: "gray",
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
  skip: "gray",
  escalated: "purple",
  escalate: "purple",
};

export function Badge({ tone = "gray", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

/** `awaiting_approval` -> an amber "awaiting approval" badge. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  return <Badge tone={STATUS_TONES[status] ?? "gray"}>{status.replaceAll("_", " ")}</Badge>;
}

/** A flag from the check node, e.g. `possible_duplicate:12` -> "possible duplicate (12)". */
export function FlagBadge({ flag }: { flag: string }) {
  const [name, detail] = flag.split(":", 2);
  const label = name.replaceAll("_", " ") + (detail ? ` (${detail})` : "");
  return <Badge tone={name === "closed" ? "gray" : "amber"}>{label}</Badge>;
}
