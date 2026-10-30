/** The audit log: URL filters -> the request, and rows link to their pages. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AuditRow } from "../api/types";
import { fakeApi, page, REVIEWER, renderApp } from "../test/render";

const ROW: AuditRow = {
  id: 1,
  occurred_at: "2026-10-30T10:00:00Z",
  actor_type: "user",
  actor: "reviewer@example.com",
  action: "approval.approved",
  entity_type: "run_item",
  entity_id: 7,
  run_id: 6,
  run_item_id: 7,
  details: { reason: null },
  correlation_id: null,
};

describe("AuditPage", () => {
  it("sends the URL's filters, with the dates as whole UTC days", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/audit": () => page([ROW]),
    });
    renderApp("/audit?run_id=6&from=2026-10-01&to=2026-10-30", { user: REVIEWER });

    expect(await screen.findByText("approval.approved")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "run item #7" })).toHaveAttribute(
      "href",
      "/run-items/7",
    );
    const query = calls.find((call) => call.url.pathname === "/api/audit")?.url.searchParams;
    expect(query?.get("run_id")).toBe("6");
    expect(query?.get("from")).toBe("2026-10-01T00:00:00Z");
    expect(query?.get("to")).toBe("2026-10-31T00:00:00Z");
  });

  it("filters by action on Enter", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/audit": () => page([ROW]),
    });
    renderApp("/audit", { user: REVIEWER });

    await userEvent.type(await screen.findByLabelText("Action"), "lead.scored{Enter}");
    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get("action")).toBe("lead.scored"));
  });
});
