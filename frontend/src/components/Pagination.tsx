/** "51–100 of 230" with previous / next buttons, under every paged list. */

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
