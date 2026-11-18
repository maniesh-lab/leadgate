/** The public demo's login sees every admin page and button, locked; nothing is sent. */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Campaign, Me } from "../api/types";
import { LOCKED_NOTE } from "../components/Locked";
import { ADMIN, fakeApi, page, REVIEWER, renderApp } from "../test/render";
import { adminAccess } from "./access";

const DEMO: Me = { ...REVIEWER, id: 7, email: "demo@leadgate.example" };

const CAMPAIGN: Campaign = {
  id: 3,
  name: "Austin dentists",
  niche: "dentist",
  location: "Austin, TX",
  sources: ["osm"],
  source_options: {},
  rubric_id: 1,
  created_by: 1,
  created_at: "2026-10-30T09:00:00Z",
  is_archived: false,
};

function demoApi(user: Me = DEMO) {
  return fakeApi({
    "GET /api/auth/me": () => user,
    "GET /api/campaigns": () => page([CAMPAIGN]),
    "GET /api/rubrics": () => page([]),
  });
}

/** Every request but the page's own reads (a locked button must never send one). */
function writes(calls: { method: string }[]) {
  return calls.filter((call) => call.method !== "GET");
}

describe("adminAccess", () => {
  const demo = { email: "Demo@Leadgate.example", password: "public" };

  it("gives admins everything, the demo login a locked view, other reviewers nothing", () => {
    expect(adminAccess(ADMIN, demo)).toBe("full");
    expect(adminAccess(DEMO, demo)).toBe("locked"); // case doesn't matter
    expect(adminAccess(REVIEWER, demo)).toBe("none");
    expect(adminAccess(DEMO, null)).toBe("none"); // no demo login set up
    expect(adminAccess(null, demo)).toBe("none");
  });
});

describe("the public demo's login", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DEMO_EMAIL", DEMO.email);
    vi.stubEnv("VITE_DEMO_PASSWORD", "approve-some-leads");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("sees New campaign, Settings and the campaign buttons, locked", async () => {
    const { calls } = demoApi();
    renderApp("/campaigns", { user: DEMO });

    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "New campaign" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Settings" })).toBeInTheDocument();

    const start = await screen.findByRole("button", { name: "Start run" });
    for (const name of ["Start run", "Export approved", "Delete"]) {
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-disabled", "true");
    }
    await userEvent.click(start);
    expect(screen.getByRole("status")).toHaveTextContent(LOCKED_NOTE);
    expect(writes(calls)).toEqual([]);
  });

  it("can fill in a new campaign, but not create it or upload a file", async () => {
    const { calls } = demoApi();
    renderApp("/campaigns/new", { user: DEMO });

    await userEvent.type(await screen.findByLabelText("Name"), "Austin dentists");
    await userEvent.type(screen.getByLabelText("Niche"), "dentist");
    await userEvent.type(screen.getByLabelText("Location"), "Austin, TX");
    expect(screen.getByRole("note")).toHaveTextContent("turned off");
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    // No submit button while locked, so Enter in a field can't send the form either.
    await userEvent.type(screen.getByLabelText("Location"), "{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent(LOCKED_NOTE);

    await userEvent.click(screen.getByRole("checkbox", { name: /CSV file/ }));
    expect(screen.getByLabelText("CSV upload")).toBeDisabled();
    expect(writes(calls)).toEqual([]);
  });

  it("sees only its own row on the users page, and never asks for the list", async () => {
    const { calls } = demoApi();
    renderApp("/settings/users", { user: DEMO });

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(2); // the header + its own row
    expect(within(table).getByText(DEMO.email)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("real people's emails");
    expect(screen.getByRole("button", { name: "Add reviewer" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(calls.some((call) => call.url.pathname === "/api/users")).toBe(false);
  });

  it("leaves another reviewer's view as it was", async () => {
    demoApi(REVIEWER);
    renderApp("/settings/rubrics", { user: REVIEWER });

    expect(await screen.findByText("This page is for admins only.")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
  });
});
