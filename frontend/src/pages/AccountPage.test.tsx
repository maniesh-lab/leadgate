/** Changing your password: what's sent, the fresh token kept, and the errors shown. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { getToken } from "../auth/token";
import { fakeApi, REVIEWER, renderApp, reply } from "../test/render";

async function fill(current: string, next: string, repeat = next) {
  await userEvent.type(await screen.findByLabelText("Current password"), current);
  await userEvent.type(screen.getByLabelText("New password"), next);
  await userEvent.type(screen.getByLabelText("Repeat new password"), repeat);
  await userEvent.click(screen.getByRole("button", { name: "Change password" }));
}

describe("AccountPage", () => {
  it("changes the password and keeps the fresh token", async () => {
    const bodies: unknown[] = [];
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "POST /api/auth/password": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        return { access_token: "fresh-token", token_type: "bearer" };
      },
    });
    renderApp("/account", { user: REVIEWER });

    await fill("old-password", "a-new-password");

    expect(await screen.findByRole("status")).toHaveTextContent("Password changed");
    expect(bodies).toEqual([{ current_password: "old-password", new_password: "a-new-password" }]);
    expect(getToken()).toBe("fresh-token");
    // Still logged in, on the same page.
    expect(screen.getByRole("heading", { name: /Your account/ })).toBeInTheDocument();
  });

  it("checks the two new passwords match before sending", async () => {
    const { calls } = fakeApi({ "GET /api/auth/me": () => REVIEWER });
    renderApp("/account", { user: REVIEWER });

    await fill("old-password", "a-new-password", "a-new-passwrd");

    expect(screen.getByRole("alert")).toHaveTextContent("don't match");
    expect(calls.some((call) => call.url.pathname === "/api/auth/password")).toBe(false);
  });

  it("shows a wrong current password without logging out", async () => {
    fakeApi({
      "GET /api/auth/me": () => REVIEWER,
      "POST /api/auth/password": () =>
        reply(400, { error: { code: "bad_request", message: "the current password is wrong" } }),
    });
    const router = renderApp("/account", { user: REVIEWER });

    await fill("not-it", "a-new-password");

    expect(await screen.findByRole("alert")).toHaveTextContent("the current password is wrong");
    await waitFor(() => expect(router.state.location.pathname).toBe("/account"));
    expect(getToken()).toBe("test-token");
  });
});
