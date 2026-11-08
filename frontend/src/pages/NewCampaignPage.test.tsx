/** The new campaign form: what it sends, and the API's refusal shown as is. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Rubric } from "../api/types";
import { ADMIN, fakeApi, page, renderApp, reply } from "../test/render";

const RUBRICS: Rubric[] = [
  {
    id: 2,
    name: "general-b2b",
    version: 1,
    rules: [],
    description: null,
    created_by: 1,
    created_at: "",
  },
  {
    id: 5,
    name: "web-agency",
    version: 2,
    rules: [],
    description: null,
    created_by: 1,
    created_at: "",
  },
];

async function fillIn() {
  await userEvent.type(await screen.findByLabelText("Name"), "Austin dentists");
  await userEvent.type(screen.getByLabelText("Niche"), "dentist");
  await userEvent.type(screen.getByLabelText("Location"), "Austin, TX");
}

const UPLOAD = {
  id: 4,
  filename: "list.csv",
  size_bytes: 40,
  row_count: 2,
  columns: ["Company", "Tel"],
  uploaded_by: 1,
  created_at: "",
};

describe("NewCampaignPage", () => {
  it("uploads the CSV, sends the campaign and goes back to the list", async () => {
    const bodies: unknown[] = [];
    const uploads: { filename: string | null; type: string; body: unknown }[] = [];
    fakeApi({
      "POST /api/uploads/csv": (url, init) => {
        const headers = init.headers as Record<string, string>;
        uploads.push({
          filename: url.searchParams.get("filename"),
          type: headers["Content-Type"],
          body: init.body,
        });
        return reply(201, UPLOAD);
      },
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
    await userEvent.click(screen.getByLabelText("CSV file"));
    const file = new File(["Company,Tel\nA,1\nB,2\n"], "list.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText("CSV upload"), file);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^Uploaded/));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Uploaded list.csv · 2 rows · columns: Company, Tel",
    );
    expect(uploads).toEqual([{ filename: "list.csv", type: "text/csv", body: file }]);
    await userEvent.type(screen.getByLabelText("CSV column mapping"), '{{"name": "Company"}');
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/campaigns"));
    expect(bodies).toEqual([
      {
        name: "Austin dentists",
        niche: "dentist",
        location: "Austin, TX",
        sources: ["osm", "csv"],
        source_options: { max_results: 20, csv_upload_id: 4, csv_mapping: { name: "Company" } },
        rubric_id: 5,
      },
    ]);
  });

  it("won't send a CSV campaign without a file, and shows a refused upload", async () => {
    const posted: unknown[] = [];
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page(RUBRICS),
      "POST /api/campaigns": (_url, init) => posted.push(init.body),
      "POST /api/uploads/csv": () =>
        reply(413, {
          error: {
            code: "content_too_large",
            message: "the file is bigger than the 1,000,000-byte limit",
          },
        }),
    });
    renderApp("/campaigns/new", { user: ADMIN });

    await fillIn();
    await userEvent.click(screen.getByLabelText("CSV file"));
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick a CSV file.");

    await userEvent.upload(screen.getByLabelText("CSV upload"), new File(["x"], "big.csv"));
    expect(await screen.findByText(/1,000,000-byte limit/)).toBeInTheDocument();
    expect(posted).toEqual([]);
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
