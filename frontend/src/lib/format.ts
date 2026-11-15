/** Small formatters the pages share: dates and durations as people read them. */

const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

/** "2026-10-30T09:15:00Z" -> "Oct 30, 2026, 3:00 PM" (in the viewer's time zone). */
export function formatDateTime(iso: string | null | undefined): string {
  return iso ? DATE_TIME.format(new Date(iso)) : "—";
}

/** 850 -> "850 ms", 4200 -> "4.2 s", 125000 -> "2 min 5 s". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const total = Math.round(ms / 1000); // 119600 ms is "2 min 0 s", not "1 min 60 s"
  return `${Math.floor(total / 60)} min ${total % 60} s`;
}
