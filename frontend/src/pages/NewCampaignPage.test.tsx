/** The new campaign form: what it sends, and the API's refusal shown as is. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Rubric } from "../api/types";
import { ADMIN, fakeApi, page, renderApp, reply } from "../test/render";

const RUBRICS: Rubric[] = [
  { id: 2, name: "general-b2b", version: 1, rules: [], created_by: 1, created_at: "" },
  { id: 5, name: "web-agency", version: 2, rules: [], created_by: 1, created_at: "" },
];

async function fillIn() {
  await userEvent.type(await screen.findByLabelText("Name"), "Austin dentists");
  await userEvent.type(screen.getByLabelText("Niche"), "dentist");
  await userEvent.type(screen.getByLabelText("Location"), "Austin, TX");
}

describe("NewCampaignPage", () => {
  it("sends the campaign and goes back to the list", async () => {
    const bodies: unknown[] = [];
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page(RUBRICS),
      "GET /api/campaigns": () => page([]),
      "POST /api/campaigns": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        return reply(201, { id: 9 });
      },
    });
    const router = renderApp("/campaigns/new", { user: ADMIN });

    await fillIn();
    await screen.findByRole("option", { name: "web-agency v2" });
    await userEvent.selectOptions(screen.getByLabelText("Scoring rubric"), "web-agency v2");
    await userEvent.type(screen.getByLabelText("Max results"), "20");
    await userEvent.click(screen.getByLabelText("CSV file on the worker's disk"));
    await userEvent.type(screen.getByLabelText("CSV path"), "/data/list.csv");
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/campaigns"));
    expect(bodies).toEqual([
      {
        name: "Austin dentists",
        niche: "dentist",
        location: "Austin, TX",
        sources: ["osm", "csv"],
        source_options: { max_results: 20, csv_path: "/data/list.csv" },
        rubric_id: 5,
      },
    ]);
  });

  it("shows why the API refused it", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page(RUBRICS),
      "POST /api/campaigns": () =>
        reply(422, {
          error: { code: "unprocessable_content", message: "no OSM tags for niche 'unicorn'" },
        }),
    });
    renderApp("/campaigns/new", { user: ADMIN });

    await fillIn();
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no OSM tags for niche 'unicorn'");
  });
});
