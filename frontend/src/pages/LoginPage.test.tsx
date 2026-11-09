/** The login page's demo login: shown only when the build sets it, and one click logs in. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getToken } from "../auth/token";
import { demoLogin } from "../lib/demo";
import { fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

describe("demo login", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("isn't shown without the demo settings", async () => {
    fakeApi({});
    renderApp("/login");

    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Demo login" })).not.toBeInTheDocument();
  });

  it("shows the demo reviewer's login and logs in with one click", async () => {
    vi.stubEnv("VITE_DEMO_EMAIL", "demo@example.com");
    vi.stubEnv("VITE_DEMO_PASSWORD", "demo-password");
    const { calls } = fakeApi({
      "POST /api/auth/login": () => ({ access_token: "demo-token", token_type: "bearer" }),
      "GET /api/auth/me": () => ({ ...REVIEWER, email: "demo@example.com" }),
      "GET /api/reports": () => [],
      "GET /api/approvals/queue": () => page([]),
    });
    const router = renderApp("/login");

    const demo = await screen.findByRole("region", { name: "Demo login" });
    expect(demo).toHaveTextContent("demo@example.com");
    expect(demo).toHaveTextContent("demo-password");

    await userEvent.click(screen.getByRole("button", { name: "Use the demo login" }));

    const login = calls.find((call) => call.url.pathname === "/api/auth/login");
    expect(String(login?.body)).toBe("username=demo%40example.com&password=demo-password");
    await vi.waitFor(() => expect(router.state.location.pathname).not.toBe("/login"));
  });

  it("needs both values", () => {
    expect(demoLogin({ VITE_DEMO_EMAIL: "demo@example.com" } as ImportMetaEnv)).toBeNull();
    expect(
      demoLogin({
        VITE_DEMO_EMAIL: " demo@example.com ",
        VITE_DEMO_PASSWORD: "pw",
      } as ImportMetaEnv),
    ).toEqual({ email: "demo@example.com", password: "pw" });
  });
});

describe("two-factor login", () => {
  it("asks for the code after the password, then logs in", async () => {
    const bodies: unknown[] = [];
    fakeApi({
      "POST /api/auth/login": () => ({
        access_token: null,
        token_type: "bearer",
        two_factor_required: true,
        two_factor_token: "step-token",
      }),
      "POST /api/auth/login/two-factor": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        return { access_token: "real-token", token_type: "bearer" };
      },
      "GET /api/auth/me": () => REVIEWER,
      "GET /api/reports": () => [],
      "GET /api/approvals/queue": () => page([]),
    });
    const router = renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Email"), "reviewer@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password-123");
    await userEvent.click(screen.getByRole("button", { name: /Log in/ }));
    await userEvent.type(await screen.findByLabelText("Code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/dashboard"));
    expect(bodies).toEqual([{ two_factor_token: "step-token", code: "123456" }]);
    expect(getToken()).toBe("real-token");
  });

  it("shows a wrong code and lets you try again", async () => {
    fakeApi({
      "POST /api/auth/login": () => ({ two_factor_required: true, two_factor_token: "step" }),
      "POST /api/auth/login/two-factor": () =>
        reply(401, { error: { code: "unauthorized", message: "wrong code" } }),
    });
    renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Email"), "reviewer@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "password-123");
    await userEvent.click(screen.getByRole("button", { name: /Log in/ }));
    await userEvent.type(await screen.findByLabelText("Code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("wrong code");
    expect(getToken()).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });
});
