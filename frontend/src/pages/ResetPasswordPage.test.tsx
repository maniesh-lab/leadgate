/** The page a one-time reset link opens: the token comes from after "#". */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { fakeApi, renderApp, reply } from "../test/render";

afterEach(() => window.history.replaceState(null, "", "/"));

async function fill(password: string, repeat = password) {
  await userEvent.type(await screen.findByLabelText("New password"), password);
  await userEvent.type(screen.getByLabelText("Repeat new password"), repeat);
  await userEvent.click(screen.getByRole("button", { name: "Set password" }));
}

describe("ResetPasswordPage", () => {
  it("sends the token and the new password, and takes the token out of the address bar", async () => {
    window.history.replaceState(null, "", "/reset-password#token=secret-token");
    const bodies: unknown[] = [];
    fakeApi({
      "POST /api/auth/reset-password": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        return reply(204, null);
      },
    });
    renderApp("/reset-password");

    await fill("a-new-password");

    expect(await screen.findByRole("status")).toHaveTextContent("Your password is set");
    expect(bodies).toEqual([{ token: "secret-token", new_password: "a-new-password" }]);
    expect(window.location.hash).toBe("");
    expect(screen.getByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login");
  });

  it("checks the passwords match, and shows the API's answer for a used link", async () => {
    window.history.replaceState(null, "", "/reset-password#token=used");
    const { calls } = fakeApi({
      "POST /api/auth/reset-password": () =>
        reply(400, { error: { code: "bad_request", message: "this link doesn't work" } }),
    });
    renderApp("/reset-password");

    await fill("a-new-password", "a-new-passwrd");
    expect(screen.getByRole("alert")).toHaveTextContent("don't match");
    expect(calls).toHaveLength(0);

    await userEvent.clear(screen.getByLabelText("Repeat new password"));
    await userEvent.type(screen.getByLabelText("Repeat new password"), "a-new-password");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("this link doesn't work");
  });

  it("says so when the link has no token", async () => {
    fakeApi({});
    renderApp("/reset-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("incomplete");
  });
});
