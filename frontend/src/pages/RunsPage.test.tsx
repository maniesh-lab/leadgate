/** The run list: its rows, and filters that live in the URL and go to the API. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Run } from "../api/types";
import { ADMIN, fakeApi, page, renderApp } from "../test/render";

function run(fields: Partial<Run> = {}): Run {
  return {
    id: 834,
    campaign_id: 35,
    campaign_name: "Kathmandu dentists",
    trigger: "manual",
    triggered_by: 1,
    rubric_id: 1,
    status: "waiting_approval",
    correlation_id: "abc",
    stats: { collected: 5, run_items: 5 },
    created_at: "2026-11-08T07:00:00Z",
    started_at: "2026-11-08T07:00:01Z",
    finished_at: null,
    ...fields,
  };
}

function runsApi(runs: Run[] = [run()]) {
  return fakeApi({
    "GET /api/auth/me": () => ADMIN,
    "GET /api/campaigns": () => page([]),
    "GET /api/runs": () => page(runs),
  });
}

/** The query strings of every `GET /api/runs` so far, without the page size. */
function runQueries(calls: { url: URL }[]): string[] {
  return calls
    .filter((call) => call.url.pathname === "/api/runs")
    .map((call) => {
      const params = new URLSearchParams(call.url.search);
      params.delete("page_size");
      return params.toString();
    });
}

describe("RunsPage", () => {
  it("lists each run with a link, its campaign, status and lead count", async () => {
    runsApi([run(), run({ id: 833, status: "completed", stats: {}, campaign_name: "derms" })]);
    renderApp("/runs", { user: ADMIN });

    const link = await screen.findByRole("link", { name: "#834" });
    expect(link).toHaveAttribute("href", "/runs/834");
    const row = link.closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("Kathmandu dentists")).toBeInTheDocument();
    expect(within(row).getByText("waiting approval")).toBeInTheDocument();
    const LEADS = 4; // the column
    expect(row.cells[LEADS]).toHaveTextContent("5");
    // A run with no stats yet shows a dash for its leads.
    const other = screen.getByRole("link", { name: "#833" }).closest("tr") as HTMLTableRowElement;
    expect(other.cells[LEADS]).toHaveTextContent("—");
  });

  it("sends the URL's filters to the API and shows them in the controls", async () => {
    const { calls } = runsApi();
    renderApp("/runs?campaign_id=35&status=completed&page=2", { user: ADMIN });

    await screen.findByRole("link", { name: "#834" });
    expect(runQueries(calls)[0]).toBe("page=2&campaign_id=35&status=completed");
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("completed");
  });

  it("puts a new status in the URL and goes back to page 1", async () => {
    const { calls } = runsApi();
    const router = renderApp("/runs?page=3", { user: ADMIN });
    await screen.findByRole("link", { name: "#834" });

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Status" }), "failed");

    await waitFor(() => expect(router.state.location.search).toBe("?status=failed"));
    await waitFor(() => expect(runQueries(calls).at(-1)).toBe("page=1&status=failed"));
  });

  it("points to the campaigns when no run matches", async () => {
    runsApi([]);
    renderApp("/runs?status=failed", { user: ADMIN });

    expect(await screen.findByText("No runs match.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to campaigns" })).toHaveAttribute(
      "href",
      "/campaigns",
    );
  });
});
