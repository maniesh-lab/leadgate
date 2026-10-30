/** What a page shows while its data loads, or when loading it failed. */
import { describeError } from "../api/client";

export function Loading() {
  return <p className="text-sm text-slate-500">Loading…</p>;
}

export function ErrorMessage({ error }: { error: unknown }) {
  return (
    <p role="alert" className="text-sm text-red-700">
      {describeError(error)}
    </p>
  );
}
