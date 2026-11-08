/** The campaign list's admin buttons: the CSV export downloads a file, delete asks first;
 * reviewers see none. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Campaign } from "../api/types";
import { ADMIN, fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

const CAMPAIGN: Campaign = {
  id: 7,
  name: "Austin dentists",
  niche: "dentist",
  location: "Austin, TX",
  sources: ["csv"],
  source_options: {},
  rubric_id: 2,
  created_by: 1,
  created_at: "2026-11-02T10:00:00Z",
  is_archived: false,
};

afterEach(() => vi.restoreAllMocks());

describe("CampaignsPage", () => {
  it("exports a campaign's approved leads as a CSV file", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page([]),
      "GET /api/campaigns": () => page([CAMPAIGN]),
      "GET /api/exports/approved-leads": () => "lead_id,name\n",
    });
    const blobUrl = vi.fn(() => "blob:leadgate");
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL: blobUrl, revokeObjectURL: vi.fn() }),
    );
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download);
    });
    renderApp("/campaigns", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Export approved" }));

    await waitFor(() => expect(saved).toEqual(["leadgate-campaign-7-approved.csv"]));
    const exported = calls.find((call) => call.url.pathname === "/api/exports/approved-leads");
    expect(exported?.url.searchParams.get("campaign_id")).toBe("7");
    expect(blobUrl).toHaveBeenCalledOnce();
  });

  it("shows a failed export", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page([]),
      "GET /api/campaigns": () => page([CAMPAIGN]),
      "GET /api/exports/approved-leads": () =>
        reply(404, { error: { code: "not_found", message: "campaign 7 not found" } }),
    });
    renderApp("/campaigns", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Export approved" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("campaign 7 not found");
  });

  it("asks before deleting a campaign, and shows the API's refusal", async () => {
    const { calls } = fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page([]),
      "GET /api/campaigns": () => page([CAMPAIGN]),
      "DELETE /api/campaigns/7": () =>
        reply(409, {
          error: {
            code: "conflict",
            message: "run 9 is still collecting leads; try again shortly",
          },
        }),
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    renderApp("/campaigns", { user: ADMIN });
    const deletes = () => calls.filter((call) => call.method === "DELETE");

    const remove = await screen.findByRole("button", { name: "Delete" });
    await userEvent.click(remove); // "Cancel" in the dialog
    expect(deletes()).toEqual([]);

    await userEvent.click(remove);
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('Delete "Austin dentists"'));
    expect(await screen.findByRole("alert")).toHaveTextContent("run 9 is still collecting leads");
    expect(deletes()).toHaveLength(1);
  });

  it("shows reviewers no admin buttons", async () => {
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/rubrics": () => page([]),
      "GET /api/campaigns": () => page([CAMPAIGN]),
    });
    renderApp("/campaigns", { user: REVIEWER });

    expect(await screen.findByText("Austin dentists")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export approved" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});
