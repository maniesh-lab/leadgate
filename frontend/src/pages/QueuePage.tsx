/**
 * The approval queue: every lead the agents finished, waiting for a human, oldest first.
 * Clicking one opens the decision dialog; after a decision the next lead opens, so a
 * reviewer can work down the list with A and R. The campaign filter and the page live
 * in the URL (`/queue?campaign_id=3`), like the other lists.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Page, QueueItem } from "../api/types";
import { FlagBadge, ScoreMeter } from "../components/Badge";
import { CampaignSelect } from "../components/CampaignSelect";
import { DecisionDialog } from "../components/DecisionDialog";
import { EmptyState, PageHeader } from "../components/Page";
import { Pagination } from "../components/Pagination";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;

export function QueuePage() {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const campaignId = params.get("campaign_id") ?? "";
  const [openId, setOpenId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const queue = useQuery({
    queryKey: ["approvals", "queue", { page, campaignId }],
    queryFn: () =>
      api.get<Page<QueueItem>>("/api/approvals/queue", {
        page,
        page_size: PAGE_SIZE,
        campaign_id: campaignId,
      }),
  });

  const items = queue.data?.items ?? [];
  const open = items.find((item) => item.run_item_id === openId);

  /** After a decision: the item below the decided one, or close at the end of the page. */
  function openNext() {
    const index = items.findIndex((item) => item.run_item_id === openId);
    setOpenId(items[index + 1]?.run_item_id ?? null);
  }

  /** Closing reloads the list: after a 409 the item may be gone (someone else decided). */
  function close() {
    setOpenId(null);
    queryClient.invalidateQueries({ queryKey: ["approvals"] });
  }

  function update(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== "page") next.delete("page");
      return next;
    });
  }

  return (
    <section>
      <PageHeader
        tone="amber"
        eyebrow="Human in the loop"
        title={
          <>
            Approval <span className="text-gradient">queue</span>
          </>
        }
        subtitle="Leads the agents enriched, checked and scored. Nothing reaches the CRM until someone here approves it."
        actions={
          queue.data && (
            <span className="font-mono text-sm text-muted">
              <span className="text-2xl font-semibold text-fg tabular-nums">
                {queue.data.total}
              </span>{" "}
              waiting
            </span>
          )
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-center">
        <CampaignSelect value={campaignId} onChange={(value) => update("campaign_id", value)} />
        <p className="font-mono text-xs text-faint">
          Open a lead, then <kbd>A</kbd> approve · <kbd>R</kbd> reject · <kbd>Esc</kbd> close
        </p>
      </div>

      {queue.isPending && <Loading />}
      {queue.error && <ErrorMessage error={queue.error} />}
      {queue.data && items.length === 0 && (
        <EmptyState
          title="Nothing to review."
          hint="New leads show up here when a run reaches them."
        />
      )}
      {items.length > 0 && queue.data && (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Campaign</th>
                  <th>Score</th>
                  <th>Flags</th>
                  <th>Waiting since</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.run_item_id}
                    className="cursor-pointer"
                    onClick={() => setOpenId(item.run_item_id)}
                  >
                    <td className="min-w-48">
                      <div className="font-medium">{item.lead.name}</div>
                      <div className="text-xs text-muted">
                        {[item.lead.category, item.lead.city].filter(Boolean).join(" · ")}
                      </div>
                    </td>
                    <td className="text-muted">{item.campaign_name}</td>
                    <td>
                      <ScoreMeter score={item.score} />
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {item.flags.map((flag) => (
                          <FlagBadge key={flag} flag={flag} />
                        ))}
                        {item.extraction_ok === false && <FlagBadge flag="extraction_failed" />}
                      </div>
                    </td>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(item.waiting_since)}
                    </td>
                    <td className="text-right">
                      <button
                        type="button"
                        className="btn px-3 py-1"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenId(item.run_item_id);
                        }}
                      >
                        Review
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={queue.data.total}
            onPage={(next) => update("page", String(next))}
          />
        </>
      )}

      {open && <DecisionDialog item={open} onClose={close} onDecided={openNext} />}
    </section>
  );
}
