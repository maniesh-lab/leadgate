/** The login page's demo login: shown only when the build sets it, and one click logs in. */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoLogin } from "../lib/demo";
import { fakeApi, page, REVIEWER, renderApp } from "../test/render";

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
