/** A run that found nothing says why: a skipped source shows its reason. An admin can
 * delete a run that has stopped moving. The page keeps checking a waiting run. */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunDetail } from "../api/types";
import { ADMIN, fakeApi, page, renderApp, reply } from "../test/render";
import { refreshEvery } from "./RunPage";

const RUN: RunDetail = {
  id: 810,
  campaign_id: 27,
  campaign_name: "derms",
  trigger: "manual",
  triggered_by: 1,
  rubric_id: 1,
  status: "completed",
  correlation_id: "abc",
  stats: {
    collected: 0,
    run_items: 0,
    skipped_sources: { google_places: "GOOGLE_PLACES_API_KEY is not set" },
    failed_sources: {},
  },
  created_at: "2026-11-04T07:01:44Z",
  started_at: "2026-11-04T07:01:45Z",
  finished_at: "2026-11-04T07:01:45Z",
  counts: {},
  items: [],
};

afterEach(() => vi.restoreAllMocks());

describe("RunPage", () => {
  it("says why a source was skipped", async () => {
    fakeApi({ "GET /api/auth/me": () => ADMIN, "GET /api/runs/810": () => RUN });
    renderApp("/runs/810", { user: ADMIN });

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Source google_places was skipped: GOOGLE_PLACES_API_KEY is not set",
    );
  });

  it("deletes a finished run and goes back to its campaign's runs", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/runs/810": () => RUN,
      "DELETE /api/runs/810": () => reply(204, null),
      "GET /api/runs": () => page([]),
      "GET /api/campaigns": () => page([]),
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/runs/810", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));

    expect(await screen.findByRole("heading", { name: /runs/i })).toBeInTheDocument();
    const runs = calls.find((call) => call.method === "GET" && call.url.pathname === "/api/runs");
    expect(runs?.url.searchParams.get("campaign_id")).toBe("27");
    expect(calls.some((call) => call.method === "DELETE")).toBe(true);
  });

  it("offers no delete while the run is still moving", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/runs/810": () => ({ ...RUN, status: "running" }),
    });
    renderApp("/runs/810", { user: ADMIN });

    expect(await screen.findByText("derms")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("keeps checking a run that waits for decisions, less often", () => {
    expect(refreshEvery("running")).toBe(3000);
    expect(refreshEvery(undefined)).toBe(3000); // still loading
    expect(refreshEvery("waiting_approval")).toBe(15000);
    expect(refreshEvery("completed")).toBe(false);
    expect(refreshEvery("failed")).toBe(false);
  });
});
