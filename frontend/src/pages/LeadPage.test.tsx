/** One lead: its fields, score card and run history; admins can opt it out or delete it. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LeadDetail } from "../api/types";
import { ADMIN, fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

function lead(fields: Partial<LeadDetail> = {}): LeadDetail {
  return {
    id: 7,
    source: "osm",
    source_ref: "node/3856445942",
    name: "Aloha Dental",
    phone: "+1 512-555-0100",
    email: "hello@aloha.example",
    website: "https://aloha.example/",
    website_domain: "aloha.example",
    address: "1 Main St",
    area: "Hyde Park",
    city: "Austin",
    country: "United States",
    category: "dentist",
    rating: null,
    review_count: null,
    score: 40,
    status: "approved",
    opt_out: false,
    enriched_at: "2026-11-01T10:00:00Z",
    first_seen_at: "2026-10-30T09:00:00Z",
    updated_at: "2026-11-01T10:00:00Z",
    flags: ["no_email"],
    services: ["Teeth Whitening", "Dental Implants"],
    established_year: 1998,
    verified: null,
    business_status: null,
    lat: null,
    lng: null,
    crm_record_id: "rec123",
    enrichment: null,
    review: {
      rubric: {
        id: 1,
        name: "general-b2b",
        version: 1,
        description: "For most B2B sales: favours reachable, active businesses.",
      },
      score_breakdown: [{ rule: "has email", points: 20, hit: true }],
      score_explanation: null,
      extraction_issues: [],
    },
    history: [
      {
        run_item_id: 55,
        run_id: 834,
        campaign_id: 35,
        campaign_name: "Austin dentists",
        status: "done",
        route: "notify",
        score: 40,
        flags: [],
        decision: "approved",
        reason: "good fit",
        reviewer_email: "reviewer@example.com",
        created_at: "2026-11-01T10:05:00Z",
      },
    ],
    ...fields,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("LeadPage", () => {
  it("shows the lead's fields, its score card and its run history", async () => {
    fakeApi({ "GET /api/auth/me": () => REVIEWER, "GET /api/leads/7": () => lead() });
    renderApp("/leads/7", { user: REVIEWER });

    expect(await screen.findByRole("heading", { name: "Aloha Dental" })).toBeInTheDocument();
    expect(screen.getByText("hello@aloha.example")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /aloha\.example/ })).toHaveAttribute(
      "href",
      "https://aloha.example/",
    );
    expect(screen.getByText("Hyde Park, Austin, United States")).toBeInTheDocument();
    expect(screen.getByText("Teeth Whitening, Dental Implants")).toBeInTheDocument();
    expect(screen.getByText("osm: node/3856445942")).toBeInTheDocument();
    expect(screen.getByText(/general-b2b v1/)).toBeInTheDocument();

    const run = screen.getByRole("link", { name: "Run #834" });
    expect(run).toHaveAttribute("href", "/run-items/55");
    const row = run.closest("tr") as HTMLElement;
    expect(within(row).getByText("Austin dentists")).toBeInTheDocument();
    expect(within(row).getByText("reviewer@example.com")).toBeInTheDocument();
    expect(within(row).getByText("“good fit”")).toBeInTheDocument();
  });

  it("says when the lead hasn't been in a run", async () => {
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/leads/7": () => lead({ history: [] }),
    });
    renderApp("/leads/7", { user: REVIEWER });

    expect(await screen.findByText("Not in any run yet.")).toBeInTheDocument();
  });

  it("shows the API's message for an unknown lead", async () => {
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/leads/7": () => reply(404, { error: { code: "not_found", message: "no lead 7" } }),
    });
    renderApp("/leads/7", { user: REVIEWER });

    expect(await screen.findByText(/no lead 7/)).toBeInTheDocument();
  });

  it("offers a reviewer neither opt-out nor delete", async () => {
    fakeApi({ "GET /api/auth/me": () => REVIEWER, "GET /api/leads/7": () => lead() });
    renderApp("/leads/7", { user: REVIEWER });

    await screen.findByRole("heading", { name: "Aloha Dental" });
    expect(screen.queryByRole("button", { name: "Opt out" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("opts the lead out after asking, then shows it opted out", async () => {
    let optedOut = false;
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/leads/7": () => lead({ opt_out: optedOut, email: optedOut ? null : "a@b.example" }),
      "DELETE /api/leads/7/data": () => {
        optedOut = true;
        return reply(204, null);
      },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/leads/7", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Opt out" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Aloha Dental"));
    expect(await screen.findByText("opted out")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Opt out" })).toBeNull();
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
  });

  it("does nothing when the opt-out isn't confirmed", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/leads/7": () => lead(),
    });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderApp("/leads/7", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Opt out" }));

    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  });

  it("deletes the lead and goes back to the lead list", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/leads/7": () => lead(),
      "DELETE /api/leads/7": () => reply(204, null),
      "GET /api/leads": () => page([]),
      "GET /api/campaigns": () => page([]),
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const router = renderApp("/leads/7", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/leads"));
    expect(
      calls.some((call) => call.method === "DELETE" && call.url.pathname === "/api/leads/7"),
    ).toBe(true);
  });
});
