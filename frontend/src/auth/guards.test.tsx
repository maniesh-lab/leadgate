/** The protected routes: login redirects, an expired token, and what each role sees. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Campaign } from "../api/types";
import { ADMIN, fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";
import { getToken } from "./token";

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

/** The API calls the campaign list makes, answered for `user`. */
function campaignsApi(user = ADMIN) {
  return {
    "GET /api/auth/me": () => user,
    "GET /api/campaigns": () => page([CAMPAIGN]),
    "GET /api/rubrics": () => page([]),
  };
}

describe("protected routes", () => {
  it("sends a logged-out visitor to the login page, then back where they were going", async () => {
    const { calls } = fakeApi({
      ...campaignsApi(),
      "POST /api/auth/login": () => ({ access_token: "new-token", token_type: "bearer" }),
      "GET /api/runs": () => page([]),
    });
    const router = renderApp("/runs?status=completed");

    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");

    await userEvent.type(screen.getByLabelText("Email"), "admin@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret-password");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("heading", { name: "Runs" })).toBeInTheDocument();
    expect(router.state.location.pathname + router.state.location.search).toBe(
      "/runs?status=completed",
    );
    expect(getToken()).toBe("new-token");
    // The login form is OAuth2's: "username" + "password", form-encoded.
    const login = calls.find((call) => call.url.pathname === "/api/auth/login");
    expect(String(login?.body)).toBe("username=admin%40example.com&password=secret-password");
  });

  it("shows the API's message when the login fails", async () => {
    fakeApi({
      "POST /api/auth/login": () =>
        reply(401, { error: { code: "unauthorized", message: "wrong email or password" } }),
    });
    renderApp("/login");

    await userEvent.type(screen.getByLabelText("Email"), "admin@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("wrong email or password");
    expect(getToken()).toBeNull();
  });

  it("logs out when the token has expired", async () => {
    fakeApi({
      "GET /api/auth/me": () =>
        reply(401, { error: { code: "unauthorized", message: "token expired" } }),
    });
    const router = renderApp("/campaigns", { user: ADMIN });

    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(getToken()).toBeNull();
  });

  it("logs out on the Log out button", async () => {
    fakeApi(campaignsApi());
    renderApp("/campaigns", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(getToken()).toBeNull();
  });
});

describe("roles", () => {
  it("shows admins the New campaign button in the menu bar and the Start run button", async () => {
    fakeApi(campaignsApi(ADMIN));
    renderApp("/campaigns", { user: ADMIN });

    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "New campaign" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Start run" })).toBeInTheDocument();
  });

  it("hides them from reviewers", async () => {
    fakeApi(campaignsApi(REVIEWER));
    renderApp("/campaigns", { user: REVIEWER });

    expect(await screen.findByText("Austin dentists")).toBeInTheDocument();
    const nav = screen.getByRole("navigation");
    expect(within(nav).queryByRole("link", { name: "New campaign" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Leads" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
  });

  it("refuses an admin-only page to a reviewer who types its URL", async () => {
    fakeApi(campaignsApi(REVIEWER));
    renderApp("/campaigns/new", { user: REVIEWER });

    expect(await screen.findByText("This page is for admins only.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create campaign" })).not.toBeInTheDocument();
  });

  it("opens the new run's page after Start run", async () => {
    fakeApi({
      ...campaignsApi(ADMIN),
      "POST /api/campaigns/3/runs": () => ({ id: 42 }),
      "GET /api/runs/42": () =>
        reply(404, { error: { code: "not_found", message: "run 42 not found" } }),
    });
    const router = renderApp("/campaigns", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Start run" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/runs/42"));
  });
});
