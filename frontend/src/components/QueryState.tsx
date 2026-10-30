/** What a page shows while its data loads, or when loading it failed. */
import { describeError } from "../api/client";

export function Loading() {
  return (
    <p className="flex items-center gap-2 text-sm text-muted">
      <span className="size-2 animate-pulse rounded-full bg-accent" />
      Loading…
    </p>
  );
}

export function ErrorMessage({ error }: { error: unknown }) {
  return (
    <p
      role="alert"
      className="rounded-xl border border-(--red)/30 bg-(--red-bg) px-4 py-3 text-sm text-(--red)"
    >
      {describeError(error)}
    </p>
  );
}
