/** The approval queue: the decision dialog, its keyboard shortcuts and its errors. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { QueueItem } from "../api/types";
import { fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

function queueItem(id: number, name: string): QueueItem {
  return {
    run_item_id: id,
    run_id: 6,
    campaign_id: 4,
    campaign_name: "Austin dentists",
    score: 65,
    flags: ["possible_duplicate:12"],
    extraction_ok: true,
    created_at: "2026-10-30T09:00:00Z",
    waiting_since: "2026-10-30T09:05:00Z",
    lead: {
      id: id * 10,
      name,
      phone: "+1 512-555-0100",
      email: null,
      website: null,
      address: "1 Main St",
      area: null,
      city: "Austin",
      category: "dentist",
      rating: null,
      review_count: null,
      score: 65,
      status: "in_review",
      opt_out: false,
    },
    review: {
      score_breakdown: [{ rule: "no website", points: 30, hit: true }],
      score_explanation: "No website, so a good fit for a new one.",
      extraction_issues: [],
    },
  };
}

/** A fake API with two waiting items; decisions go to `decisions`. */
function setup(decide: (id: number) => unknown = () => reply(200, {})) {
  const decisions: { id: number; body: unknown }[] = [];
  const { calls } = fakeApi({
    "GET /api/auth/me": () => REVIEWER,
    "GET /api/campaigns": () => page([]),
    "GET /api/approvals/queue": () =>
      page([queueItem(1, "Bright Smiles"), queueItem(2, "Lake Dental")]),
    "GET /api/leads/10": () => ({ enrichment: null }),
    "GET /api/leads/20": () => ({ enrichment: null }),
    "POST /api/run-items/1/decision": (_url, init) => {
      decisions.push({ id: 1, body: JSON.parse(String(init.body)) });
      return decide(1);
    },
  });
  renderApp("/queue", { user: REVIEWER });
  return { decisions, calls };
}

async function openFirst() {
  await userEvent.click(await screen.findByText("Bright Smiles"));
  return screen.getByRole("dialog");
}

describe("QueuePage", () => {
  it("shows the lead card with flags and the score breakdown", async () => {
    setup();
    const dialog = await openFirst();
    expect(within(dialog).getByText("possible duplicate (12)")).toBeInTheDocument();
    expect(
      within(dialog).getByText("No website, so a good fit for a new one."),
    ).toBeInTheDocument();
    expect(await within(dialog).findByText("No website.")).toBeInTheDocument();
  });

  it("approves with the A key, then opens the next lead", async () => {
    const { decisions } = setup();
    await openFirst();
    await userEvent.keyboard("a");

    await waitFor(() =>
      expect(decisions).toEqual([{ id: 1, body: { decision: "approved", reason: null } }]),
    );
    expect(
      await within(screen.getByRole("dialog")).findByRole("heading", { name: "Lake Dental" }),
    ).toBeInTheDocument();
  });

  it("won't reject without a reason", async () => {
    const { decisions } = setup();
    const dialog = await openFirst();
    await userEvent.click(within(dialog).getByRole("button", { name: /Reject/ }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent("Write a reason");
    expect(decisions).toEqual([]);

    // Typing an "r" in the reason box is text, not the shortcut.
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "a chain, not a practice");
    expect(decisions).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: /Reject/ }));
    await waitFor(() =>
      expect(decisions).toEqual([
        { id: 1, body: { decision: "rejected", reason: "a chain, not a practice" } },
      ]),
    );
  });

  it("says so when someone else decided first", async () => {
    setup(() =>
      reply(409, { error: { code: "conflict", message: "run item 1 is not awaiting approval" } }),
    );
    const dialog = await openFirst();
    await userEvent.click(within(dialog).getByRole("button", { name: /Approve/ }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("not awaiting approval");
  });

  it("closes with Esc", async () => {
    setup();
    await openFirst();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
