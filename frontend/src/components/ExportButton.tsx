/**
 * "Export approved": downloads a run's or a campaign's approved leads as a CSV file
 * (`GET /api/exports/approved-leads`, admins only). The file comes through `fetch` with
 * the login token, then the browser saves it under `filename`.
 */
import { useMutation } from "@tanstack/react-query";
import { api, describeError } from "../api/client";
import { saveFile } from "../lib/download";
import { DownloadIcon } from "./icons";

type Props = { target: { run_id: number } | { campaign_id: number }; filename: string };

export function ExportButton({ target, filename }: Props) {
  const exportCsv = useMutation({
    mutationFn: () => api.download("/api/exports/approved-leads", target),
    onSuccess: (blob) => saveFile(blob, filename),
  });

  return (
    <div>
      <button
        type="button"
        className="btn px-3 py-1.5 whitespace-nowrap"
        title="The approved leads as a CSV file (opted-out leads left out)"
        disabled={exportCsv.isPending}
        onClick={() => exportCsv.mutate()}
      >
        <DownloadIcon className="size-3.5" />
        {exportCsv.isPending ? "Exporting…" : "Export approved"}
      </button>
      {exportCsv.error && (
        <p role="alert" className="mt-1 text-xs text-(--red)">
          {describeError(exportCsv.error)}
        </p>
      )}
    </div>
  );
}
