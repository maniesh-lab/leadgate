/** A failed lead's Retry button: admins only, it queues the lead and shows where it is now. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { RunItemDetail } from "../api/types";
import { ADMIN, fakeApi, REVIEWER, renderApp, reply } from "../test/render";

function failedItem(changes: Partial<RunItemDetail> = {}): RunItemDetail {
  return {
    id: 5,
    run_id: 2,
    campaign_id: 1,
    thread_id: "run-2-lead-9",
    status: "failed",
    route: null,
    score: null,
    flags: [],
    extraction_ok: null,
    notified_at: null,
    error: "RuntimeError: LLM down",
    created_at: "2026-11-02T10:00:00Z",
    updated_at: "2026-11-02T10:05:00Z",
    lead: {
      id: 9,
      name: "Smile Dental",
      phone: null,
      email: null,
      website: null,
      address: null,
      area: null,
      city: "Austin",
      category: "dentist",
      rating: null,
      review_count: null,
      score: null,
      status: "new",
      opt_out: false,
    },
    review: { score_breakdown: [], score_explanation: null, extraction_issues: [] },
    steps: [],
    approval: null,
    ...changes,
  };
}

describe("RunItemPage", () => {
  it("retries a failed lead and shows it queued again", async () => {
    let current = failedItem();
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/run-items/5": () => current,
      "POST /api/run-items/5/retry": () => {
        current = failedItem({ status: "pending", error: null });
        return { ...current, lead_id: 9, lead_name: "Smile Dental" };
      },
    });
    renderApp("/run-items/5", { user: ADMIN });

    expect(await screen.findByText("RuntimeError: LLM down")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.getByText("pending")).toBeInTheDocument());
    expect(screen.queryByText("RuntimeError: LLM down")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("shows why a retry was refused", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/run-items/5": () => failedItem(),
      "POST /api/run-items/5/retry": () =>
        reply(409, {
          error: {
            code: "conflict",
            message: "lead 9 opted out; its approved item can't be retried",
          },
        }),
    });
    renderApp("/run-items/5", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));

    // (The item's own error is an alert too.)
    expect(await screen.findByText(/lead 9 opted out/)).toHaveAttribute("role", "alert");
  });

  it("shows reviewers no Retry button", async () => {
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/run-items/5": () => failedItem(),
    });
    renderApp("/run-items/5", { user: REVIEWER });

    expect(await screen.findByText("RuntimeError: LLM down")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});
