/** The lead table's filters: read from the URL, sent to the API, changed into the URL. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { LeadRow } from "../api/types";
import { ADMIN, fakeApi, page, renderApp } from "../test/render";

function leadRow(fields: Partial<LeadRow>): LeadRow {
  return {
    id: 1,
    name: "Aloha Dental",
    phone: null,
    email: null,
    website: null,
    address: null,
    area: null,
    city: "Austin",
    category: "dentist",
    rating: null,
    review_count: null,
    score: 40,
    status: "in_review",
    opt_out: false,
    source: "osm",
    country: "US",
    website_domain: null,
    enriched_at: null,
    first_seen_at: "2026-10-30T09:00:00Z",
    updated_at: "2026-10-30T09:00:00Z",
    flags: [],
    ...fields,
  };
}

function leadsApi(rows: LeadRow[] = []) {
  return fakeApi({
    "GET /api/auth/me": () => ADMIN,
    "GET /api/campaigns": () => page([]),
    "GET /api/leads": () => page(rows),
  });
}

/** The query strings of every `GET /api/leads` so far, without paging. */
function leadQueries(calls: { url: URL }[]): string[] {
  return calls
    .filter((call) => call.url.pathname === "/api/leads")
    .map((call) => {
      const params = new URLSearchParams(call.url.search);
      params.delete("page");
      params.delete("page_size");
      return params.toString();
    });
}

describe("LeadsPage", () => {
  it("sends the URL's filters to the API and shows them in the controls", async () => {
    const { calls } = leadsApi();
    renderApp("/leads?status=approved&has_email=true&sort=-score&q=dental&page=2", {
      user: ADMIN,
    });

    expect(await screen.findByText("No leads match.")).toBeInTheDocument();
    const lead = calls.find((call) => call.url.pathname === "/api/leads");
    expect(Object.fromEntries(lead?.url.searchParams ?? [])).toEqual({
      status: "approved",
      has_email: "true",
      sort: "-score",
      q: "dental",
      page: "2",
      page_size: "50",
    });
    expect(screen.getByLabelText("Status")).toHaveValue("approved");
    expect(screen.getByLabelText("Email")).toHaveValue("true");
    expect(screen.getByLabelText("Sort")).toHaveValue("-score");
    expect(screen.getByLabelText("Search by name")).toHaveValue("dental");
  });

  it("puts a changed filter in the URL, back on page 1, and asks again", async () => {
    const { calls } = leadsApi();
    const router = renderApp("/leads?q=dental&page=3", { user: ADMIN });
    await screen.findByText("No leads match.");

    await userEvent.selectOptions(screen.getByLabelText("Status"), "escalated");
    await userEvent.selectOptions(screen.getByLabelText("Flag"), "possible_duplicate");

    await waitFor(() =>
      expect(router.state.location.search).toBe(
        "?q=dental&status=escalated&flag=possible_duplicate",
      ),
    );
    await waitFor(() =>
      expect(leadQueries(calls).at(-1)).toBe("status=escalated&flag=possible_duplicate&q=dental"),
    );
  });

  it("searches after you stop typing", async () => {
    const { calls } = leadsApi();
    const router = renderApp("/leads", { user: ADMIN });
    await screen.findByText("No leads match.");

    await userEvent.type(screen.getByLabelText("Search by name"), "castle");

    await waitFor(() => expect(router.state.location.search).toBe("?q=castle"));
    // One request for the whole word, not one per letter.
    await waitFor(() => expect(leadQueries(calls)).toEqual(["", "q=castle"]));
  });

  it("applies a score range when you leave the box", async () => {
    const { calls } = leadsApi();
    const router = renderApp("/leads", { user: ADMIN });
    await screen.findByText("No leads match.");

    await userEvent.type(screen.getByLabelText("Min score"), "60");
    await userEvent.tab();

    await waitFor(() => expect(router.state.location.search).toBe("?min_score=60"));
    await waitFor(() => expect(leadQueries(calls).at(-1)).toBe("min_score=60"));
  });

  it("clears every filter", async () => {
    leadsApi();
    const router = renderApp("/leads?status=new&flag=closed&q=x", { user: ADMIN });
    await screen.findByText("No leads match.");

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(router.state.location.search).toBe(""));
    expect(screen.getByLabelText("Search by name")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("shows which contacts were found and the latest run's flags", async () => {
    leadsApi([
      leadRow({
        id: 7,
        name: "Castle Dental",
        email: "info@castle.test",
        phone: "+15125550100",
        flags: ["possible_duplicate:11", "category_mismatch"],
      }),
    ]);
    renderApp("/leads", { user: ADMIN });

    const link = await screen.findByRole("link", { name: "Castle Dental" });
    expect(link).toHaveAttribute("href", "/leads/7");
    const row = link.closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("email");
    expect(row).toHaveTextContent("phone");
    expect(row).not.toHaveTextContent("website");
    expect(row).toHaveTextContent("possible duplicate (11)");
    expect(row).toHaveTextContent("category mismatch");
  });
});
