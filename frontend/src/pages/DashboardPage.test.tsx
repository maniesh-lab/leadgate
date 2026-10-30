/** The dashboard: headline numbers from the reports, and the footer under it. */
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

function report(rows: unknown[]) {
  return { name: "x", description: "", params: {}, rows };
}

describe("DashboardPage", () => {
  it("is the start page and adds up the daily funnel", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/ready": () => ({ status: "ok", db: "ok" }),
      "GET /api/approvals/queue": () => ({ ...page([]), total: 4 }),
      "GET /api/reports/daily_funnel": () =>
        report([
          { day: "2026-10-29", collected: 10, approved: 3, rejected: 1, escalated: 1 },
          { day: "2026-10-30", collected: 5, approved: 3, rejected: 1, escalated: 0 },
        ]),
      "GET /api/reports/approved_running_total": () =>
        report([{ day: "2026-10-30", approved: 3, running_total: 40 }]),
      "GET /api/reports/score_distribution": () => report([]),
      "GET /api/reports/reviewer_stats": () => report([]),
      "GET /api/reports/campaign_conversion": () => report([]),
      "GET /api/reports/node_durations": () => report([]),
      "GET /api/reports/top_leads_per_area": () =>
        reply(501, { error: { code: "not_implemented", message: "reports need Postgres" } }),
    });
    const router = renderApp("/", { user: REVIEWER });

    expect(await screen.findByText("75% of decisions")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/dashboard");
    expect(screen.getByText("15")).toBeInTheDocument(); // collected
    expect(screen.getByText("40")).toBeInTheDocument(); // approved, all time
    expect(screen.getByText("4")).toBeInTheDocument(); // waiting
    expect(await screen.findByText("reports need Postgres")).toBeInTheDocument();
    const funnel = calls.find((call) => call.url.pathname === "/api/reports/daily_funnel");
    expect(funnel?.url.searchParams.get("days")).toBe("30");

    const footer = screen.getByRole("contentinfo");
    expect(await within(footer).findByText("API ok")).toBeInTheDocument();
    expect(
      within(footer).getByRole("link", { name: "© OpenStreetMap contributors" }),
    ).toHaveAttribute("href", "https://www.openstreetmap.org/copyright");
  });
});
