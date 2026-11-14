/** "51–100 of 230" with previous / next buttons, under every paged list. */
import { useEffect } from "react";

type Props = {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
};

export function Pagination({ page, pageSize, total, onPage }: Props) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="mt-4 flex items-center justify-between gap-3 text-sm">
      <span className="font-mono text-xs text-faint">
        {first}–{last} of {total}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn px-3 py-1"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn px-3 py-1"
          disabled={page >= lastPage}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

/** A page past the end (its last rows were decided or deleted meanwhile) moves back to the
 * last page that has rows, instead of an empty list with no buttons to leave it. `onPage`
 * should replace the history entry: Back would otherwise land on the empty page again,
 * which moves forward again. Pass no `data` while it's a placeholder (another query's
 * rows shown while this one loads). */
export function useStayOnAPageWithRows(
  page: number,
  pageSize: number,
  data: { items: unknown[]; total: number } | undefined,
  onPage: (page: number) => void,
) {
  const lastPage = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const pastTheEnd = data !== undefined && data.items.length === 0 && page > lastPage;
  useEffect(() => {
    if (pastTheEnd) onPage(lastPage);
  }, [pastTheEnd, lastPage, onPage]);
}
