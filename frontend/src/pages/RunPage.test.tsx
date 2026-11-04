/** A run that found nothing says why: a skipped source shows its reason. */
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RunDetail } from "../api/types";
import { ADMIN, fakeApi, renderApp } from "../test/render";

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

describe("RunPage", () => {
  it("says why a source was skipped", async () => {
    fakeApi({ "GET /api/auth/me": () => ADMIN, "GET /api/runs/810": () => RUN });
    renderApp("/runs/810", { user: ADMIN });

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Source google_places was skipped: GOOGLE_PLACES_API_KEY is not set",
    );
  });
});
