/** The approval queue: the sort, the decision dialog, its keyboard shortcuts and errors,
 * and going on past the end of a page. */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
      source: "osm",
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
      rubric: {
        id: 5,
        name: "web-agency",
        version: 2,
        description: "For selling websites: favours businesses with no website.",
      },
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
    // Once item 1 is decided (by us or, on a 409, by someone else) it leaves the queue.
    "GET /api/approvals/queue": () =>
      page([
        ...(decisions.length === 0 ? [queueItem(1, "Bright Smiles")] : []),
        queueItem(2, "Lake Dental"),
      ]),
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
    // Which rules scored it, and who they suit, so "no website +30" makes sense.
    expect(within(dialog).getByText(/Scored with/)).toHaveTextContent(
      "Scored with web-agency v2: For selling websites: favours businesses with no website.",
    );
  });

  it("opens newest first and sorts by score from the menu", async () => {
    const { calls } = setup();
    const sorts = () =>
      calls
        .filter((call) => call.url.pathname === "/api/approvals/queue")
        .map((call) => call.url.searchParams.get("sort"));
    const menu = await screen.findByLabelText("Sort");
    expect(menu).toHaveValue("-created_at");
    expect(sorts()).toEqual([null]); // the default isn't sent

    await userEvent.selectOptions(menu, "Highest score first");

    await waitFor(() => expect(sorts()).toContain("-score"));
  });

  it("goes on to the next page's first lead after the last one on this page", async () => {
    let decided = false;
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/campaigns": () => page([]),
      // One lead per page: once it's decided, the next page's lead moves up into page 1.
      "GET /api/approvals/queue": () =>
        page([decided ? queueItem(2, "Lake Dental") : queueItem(1, "Bright Smiles")]),
      "GET /api/leads/10": () => ({ enrichment: null }),
      "GET /api/leads/20": () => ({ enrichment: null }),
      "POST /api/run-items/1/decision": () => {
        decided = true;
        return reply(200, {});
      },
    });
    renderApp("/queue", { user: REVIEWER });
    await openFirst();

    await userEvent.keyboard("a");

    expect(
      await within(screen.getByRole("dialog")).findByRole("heading", { name: "Lake Dental" }),
    ).toBeInTheDocument();
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

  it("ignores a held A key's repeats, so the next lead isn't approved unseen", async () => {
    const { calls } = setup();
    await openFirst();
    await userEvent.keyboard("a");
    await within(screen.getByRole("dialog")).findByRole("heading", { name: "Lake Dental" });

    fireEvent.keyDown(window, { key: "a", repeat: true }); // the key is still held down

    await new Promise((resolve) => setTimeout(resolve, 50));
    const decided = calls.filter((call) => call.url.pathname.endsWith("/decision"));
    expect(decided.map((call) => call.url.pathname)).toEqual(["/api/run-items/1/decision"]);
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
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Someone else got here first: run item 1 is not awaiting approval",
    );

    // Closing reloads the queue, which no longer holds the item.
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByText("Lake Dental")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Bright Smiles")).not.toBeInTheDocument());
  });

  it("keeps the keyboard focus in the dialog, and gives it back on close", async () => {
    setup();
    const review = (await screen.findAllByRole("button", { name: "Review" }))[0];
    await userEvent.click(review);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus(); // moved in when it opened

    const approve = within(dialog).getByRole("button", { name: /Approve/ });
    approve.focus(); // the last control
    await userEvent.tab();
    expect(within(dialog).getByRole("link", { name: "See every step" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(approve).toHaveFocus(); // and back round, never onto the page behind

    await userEvent.keyboard("{Escape}");
    expect(review).toHaveFocus();
  });

  it("tells a visitor on the shared demo login that reasons aren't kept", async () => {
    vi.stubEnv("VITE_DEMO_EMAIL", REVIEWER.email);
    vi.stubEnv("VITE_DEMO_PASSWORD", "public");
    try {
      setup();
      const dialog = await openFirst();
      expect(within(dialog).getByText(/the reason you type isn't kept/)).toBeInTheDocument();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("stays closed when a decision lands after the dialog was closed", async () => {
    // The decision's answer arrives a moment after Esc.
    const slow = () =>
      new Response(
        new ReadableStream({
          start(controller) {
            setTimeout(() => {
              controller.enqueue(new TextEncoder().encode("{}"));
              controller.close();
            }, 100);
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    setup(slow);
    const dialog = await openFirst();
    fireEvent.click(within(dialog).getByRole("button", { name: /Approve/ }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.queryByRole("dialog")).toBeNull(); // not reopened on the next lead
  });

  it("goes back a page when the last lead on this one was decided", async () => {
    let decided = false;
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/campaigns": () => page([]),
      // 51 waiting: one on page 2. Once it's decided, page 2 is past the end.
      "GET /api/approvals/queue": (url) => {
        const onPage2 = url.searchParams.get("page") === "2";
        if (onPage2)
          return decided
            ? { ...page([]), total: 50 }
            : { ...page([queueItem(1, "Bright Smiles")]), total: 51 };
        return { ...page([queueItem(2, "Lake Dental")]), total: decided ? 50 : 51 };
      },
      "GET /api/leads/10": () => ({ enrichment: null }),
      "GET /api/leads/20": () => ({ enrichment: null }),
      "POST /api/run-items/1/decision": () => {
        decided = true;
        return reply(200, {});
      },
    });
    const router = renderApp("/queue?page=2", { user: REVIEWER });
    await openFirst();
    await userEvent.keyboard("a");

    expect(await screen.findByText("Lake Dental")).toBeInTheDocument();
    expect(router.state.location.search).not.toContain("page=2");
  });

  it("closes with Esc", async () => {
    setup();
    await openFirst();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
