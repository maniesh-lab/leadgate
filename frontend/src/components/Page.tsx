/**
 * Building blocks every page uses: the page header (small label, big title, a line of
 * explanation, buttons on the right), the empty state, the "label above value"
 * facts of the detail pages, and the big-number stats of the run page.
 */
import type { ReactNode } from "react";

type HeaderProps = {
  eyebrow: string;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
};

export function PageHeader({ eyebrow, title, subtitle, actions }: HeaderProps) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <p className="eyebrow mb-3">{eyebrow}</p>
        <h1 className="text-4xl leading-tight font-semibold tracking-tight sm:text-5xl">{title}</h1>
        {subtitle && <p className="mt-3 max-w-2xl text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-line-strong px-6 py-12 text-center">
      <p className="font-medium">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Fact({ label, value }: { label: string; value: ReactNode }) {
  const empty = value === null || value === undefined || value === "";
  return (
    <div className="min-w-0">
      <div className="eyebrow mb-1 text-[10px]">{label}</div>
      <div className={`text-sm break-words ${empty ? "text-faint" : ""}`}>
        {empty ? "—" : value}
      </div>
    </div>
  );
}

/**
 * A big number with a label under it, for run stats and the dashboard. `tone` (a CSS
 * colour, e.g. "var(--approved)") puts a dot of the topic's colour before the label;
 * the number stays in the text colour.
 */
export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="eyebrow mt-1 flex items-center gap-1.5 text-[10px]">
        {tone && <Dot color={tone} />}
        {label}
      </div>
    </div>
  );
}

/** A small round mark in a topic colour, next to the label that names it. */
export function Dot({ color }: { color: string }) {
  return (
    <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: color }} />
  );
}
