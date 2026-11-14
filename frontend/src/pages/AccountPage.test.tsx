/** Changing your password: what's sent, the fresh token kept, and the errors shown. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { getToken } from "../auth/token";
import { fakeApi, REVIEWER, renderApp, reply } from "../test/render";

async function fill(current: string, next: string, repeat = next) {
  const form = within(await screen.findByRole("form", { name: "Change password" }));
  await userEvent.type(form.getByLabelText("Current password"), current);
  await userEvent.type(form.getByLabelText("New password"), next);
  await userEvent.type(form.getByLabelText("Repeat new password"), repeat);
  await userEvent.click(form.getByRole("button", { name: "Change password" }));
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

describe("two-factor login", () => {
  it("sets it up: password, then the QR code, then a code from the app", async () => {
    const bodies: Record<string, unknown> = {};
    let enabled = false;
    fakeApi({
      "GET /api/auth/me": () => ({ ...REVIEWER, two_factor_enabled: enabled }),
      "POST /api/auth/two-factor/setup": (_url, init) => {
        bodies.setup = JSON.parse(String(init.body));
        return {
          secret: "JBSWY3DPEHPK3PXP",
          otpauth_uri: "otpauth://totp/leadgate:reviewer@example.com?secret=JBSWY3DPEHPK3PXP",
          qr_svg: "data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C/svg%3E",
        };
      },
      "POST /api/auth/two-factor/enable": (_url, init) => {
        bodies.enable = JSON.parse(String(init.body));
        enabled = true;
        return { access_token: "fresh-token", token_type: "bearer" };
      },
    });
    renderApp("/account", { user: REVIEWER });

    const form = within(await screen.findByRole("form", { name: "Two-factor login" }));
    await userEvent.type(form.getByLabelText("Current password"), "password-123");
    await userEvent.click(form.getByRole("button", { name: "Set up two-factor" }));
    expect(await form.findByRole("img", { name: /QR code/ })).toBeInTheDocument();
    expect(form.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    await userEvent.type(form.getByLabelText("Code from the app"), "123456");
    await userEvent.click(form.getByRole("button", { name: "Turn on" }));

    expect(await form.findByRole("status")).toHaveTextContent("Two-factor login is on");
    expect(bodies).toEqual({ setup: { password: "password-123" }, enable: { code: "123456" } });
    expect(getToken()).toBe("fresh-token");
    expect(await form.findByRole("button", { name: "Turn off two-factor" })).toBeInTheDocument();
  });

  it("says it's off after turning it on and off in one visit", async () => {
    let enabled = false;
    fakeApi({
      "GET /api/auth/me": () => ({ ...REVIEWER, two_factor_enabled: enabled }),
      "POST /api/auth/two-factor/setup": () => ({
        secret: "JBSWY3DPEHPK3PXP",
        otpauth_uri: "otpauth://totp/leadgate:reviewer@example.com?secret=JBSWY3DPEHPK3PXP",
        qr_svg: "data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C/svg%3E",
      }),
      "POST /api/auth/two-factor/enable": () => {
        enabled = true;
        return { access_token: "token-2", token_type: "bearer" };
      },
      "POST /api/auth/two-factor/disable": () => {
        enabled = false;
        return { access_token: "token-3", token_type: "bearer" };
      },
    });
    renderApp("/account", { user: REVIEWER });
    const form = within(await screen.findByRole("form", { name: "Two-factor login" }));
    await userEvent.type(form.getByLabelText("Current password"), "password-123");
    await userEvent.click(form.getByRole("button", { name: "Set up two-factor" }));
    await userEvent.type(await form.findByLabelText("Code from the app"), "123456");
    await userEvent.click(form.getByRole("button", { name: "Turn on" }));
    await form.findByRole("button", { name: "Turn off two-factor" });

    await userEvent.type(form.getByLabelText("Current password"), "password-123");
    await userEvent.type(form.getByLabelText("Code from the app"), "654321");
    await userEvent.click(form.getByRole("button", { name: "Turn off two-factor" }));

    await form.findByRole("button", { name: "Set up two-factor" });
    expect(form.getByRole("status")).toHaveTextContent("Two-factor login is off");
  });
});
