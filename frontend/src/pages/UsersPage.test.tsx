/** The Users page: list, add a reviewer, turn an account off (asks first) or on. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UserRow } from "../api/types";
import { ADMIN, fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

const SAM: UserRow = { ...REVIEWER, id: 3, email: "sam@agency.test" };
const OLD: UserRow = { ...REVIEWER, id: 4, email: "old@agency.test", is_active: false };

afterEach(() => vi.restoreAllMocks());

function row(email: string) {
  // (Your own email is in the menu bar too, so look inside the table.)
  return within(screen.getByRole("table")).getByText(email).closest("tr") as HTMLElement;
}

describe("UsersPage", () => {
  it("lists users, with no button on your own row", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN, SAM, OLD]),
    });
    renderApp("/settings/users", { user: ADMIN });

    await screen.findByText(SAM.email);
    expect(within(row(ADMIN.email)).getByText("· you")).toBeInTheDocument();
    expect(within(row(ADMIN.email)).queryByRole("button")).toBeNull();
    expect(within(row(SAM.email)).getByText("active")).toBeInTheDocument();
    expect(within(row(SAM.email)).getByRole("button", { name: "Turn off" })).toBeInTheDocument();
    expect(within(row(OLD.email)).getByText("off")).toBeInTheDocument();
    expect(within(row(OLD.email)).getByRole("button", { name: "Turn on" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Rubrics" })).toHaveAttribute(
      "href",
      "/settings/rubrics",
    );
  });

  it("adds a reviewer", async () => {
    const bodies: unknown[] = [];
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN]),
      "POST /api/users": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        return reply(201, SAM);
      },
    });
    renderApp("/settings/users", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Add reviewer" }));
    await userEvent.type(screen.getByLabelText("Email"), SAM.email);
    await userEvent.type(screen.getByLabelText("Password"), "long-enough-1");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(bodies).toEqual([{ email: SAM.email, password: "long-enough-1", role: "reviewer" }]),
    );
    await waitFor(() => expect(screen.queryByLabelText("Password")).toBeNull());
  });

  it("asks before turning an account off, and shows the API's refusal", async () => {
    const patches: unknown[] = [];
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN, SAM]),
      "PATCH /api/users/3": (_url, init) => {
        patches.push(JSON.parse(String(init.body)));
        return reply(409, {
          error: { code: "conflict", message: "the last active admin can't be turned off" },
        });
      },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    renderApp("/settings/users", { user: ADMIN });

    const turnOff = await screen.findByRole("button", { name: "Turn off" });
    await userEvent.click(turnOff); // "Cancel" in the dialog
    expect(patches).toEqual([]);

    await userEvent.click(turnOff);
    expect(confirm).toHaveBeenLastCalledWith(
      "Turn off sam@agency.test? Their logins end right away.",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "the last active admin can't be turned off",
    );
    expect(patches).toEqual([{ is_active: false }]);
  });

  it("is admins only", async () => {
    fakeApi({ "GET /api/auth/me": () => REVIEWER });
    renderApp("/settings/users", { user: REVIEWER });

    expect(await screen.findByText(/admins only/i)).toBeInTheDocument();
  });
});

describe("UsersPage recovery", () => {
  it("makes a one-time reset link with the token after #", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN, SAM]),
      "POST /api/users/3/reset-link": () => ({
        token: "one-time-token",
        expires_at: "2026-11-09T10:30:00Z",
      }),
    });
    renderApp("/settings/users", { user: ADMIN });

    await userEvent.click(
      await within(await findRow(SAM.email)).findByRole("button", { name: "Reset link" }),
    );

    const link = await screen.findByLabelText(`Reset link for ${SAM.email}`);
    expect(link).toHaveValue(`${window.location.origin}/reset-password#token=one-time-token`);
    expect(screen.getByText(/Works once/)).toBeInTheDocument();
    // No clipboard here (as on a plain-http address): it says so instead of nothing.
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Copy failed: select it and copy")).toBeInTheDocument();
  });

  it("shows a lock and two-factor, and unlocks or turns two-factor off", async () => {
    const posted: string[] = [];
    const locked: UserRow = {
      ...SAM,
      locked_until: "2026-11-09T10:15:00Z",
      two_factor_enabled: true,
    };
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN, locked]),
      "POST /api/users/3/unlock": (url) => {
        posted.push(url.pathname);
        return { ...locked, locked_until: null };
      },
      "POST /api/users/3/two-factor/off": (url) => {
        posted.push(url.pathname);
        return { ...locked, two_factor_enabled: false };
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/settings/users", { user: ADMIN });

    const samRow = within(await findRow(SAM.email));
    expect(samRow.getByText("2FA")).toBeInTheDocument();
    expect(samRow.getByText(/locked until/)).toBeInTheDocument();
    await userEvent.click(samRow.getByRole("button", { name: "Unlock" }));
    await userEvent.click(samRow.getByRole("button", { name: "Turn off 2FA" }));

    await waitFor(() =>
      expect(posted).toEqual(["/api/users/3/unlock", "/api/users/3/two-factor/off"]),
    );
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("Turn off two-factor"));
  });
});

describe("UsersPage, another admin", () => {
  const ANN: UserRow = { ...ADMIN, id: 5, email: "ann@agency.test", two_factor_enabled: true };

  it("asks for your password and code before their reset link or two-factor off", async () => {
    const bodies: Record<string, unknown> = {};
    fakeApi({
      "GET /api/auth/me": () => ({ ...ADMIN, two_factor_enabled: true }),
      "GET /api/users": () => page([ADMIN, ANN]),
      "POST /api/users/5/reset-link": (_url, init) => {
        bodies.link = JSON.parse(String(init.body));
        return { token: "t", expires_at: "2026-11-09T10:30:00Z" };
      },
      "POST /api/users/5/two-factor/off": (_url, init) => {
        bodies.off = JSON.parse(String(init.body));
        return { ...ANN, two_factor_enabled: false };
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/settings/users", { user: { ...ADMIN, two_factor_enabled: true } });

    const annRow = within(await findRow(ANN.email));
    await userEvent.click(annRow.getByRole("button", { name: "Reset link" }));
    expect(bodies).toEqual({}); // nothing sent before the password
    await userEvent.type(annRow.getByLabelText("Your password"), "my-password");
    await userEvent.type(annRow.getByLabelText("Your code"), "123456");
    await userEvent.click(annRow.getByRole("button", { name: "Make link" }));
    await screen.findByLabelText(`Reset link for ${ANN.email}`);

    await userEvent.click(annRow.getByRole("button", { name: "Turn off 2FA" }));
    await userEvent.type(annRow.getByLabelText("Your password"), "my-password");
    await userEvent.type(annRow.getByLabelText("Your code"), "654321");
    await userEvent.click(annRow.getByRole("button", { name: "Turn off 2FA" }));

    await waitFor(() =>
      expect(bodies).toEqual({
        link: { password: "my-password", code: "123456" },
        off: { password: "my-password", code: "654321" },
      }),
    );
  });

  it("asks for your password before turning a former admin back on", async () => {
    let active = false;
    const bodies: unknown[] = [];
    const formerAdmin: UserRow = { ...ANN, is_active: false, two_factor_enabled: false };
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN, { ...formerAdmin, is_active: active }]),
      "PATCH /api/users/5": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        active = true;
        return { ...formerAdmin, is_active: true };
      },
    });
    renderApp("/settings/users", { user: ADMIN });
    const annRow = within(await findRow(ANN.email));

    await userEvent.click(annRow.getByRole("button", { name: "Turn on" }));
    await userEvent.type(annRow.getByLabelText("Your password"), "my-password");
    await userEvent.click(annRow.getByRole("button", { name: "Turn on" }));

    expect(await annRow.findByRole("button", { name: "Turn off" })).toBeInTheDocument();
    expect(annRow.queryByLabelText("Your password")).toBeNull(); // the prompt is gone
    expect(bodies).toEqual([{ is_active: true, confirm: { password: "my-password", code: null } }]);
  });

  it("moves keyboard focus into the password prompt, and back on Cancel", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/users": () => page([ADMIN, ANN]),
    });
    renderApp("/settings/users", { user: ADMIN });
    const annRow = within(await findRow(ANN.email));
    const button = annRow.getByRole("button", { name: "Reset link" });

    await userEvent.click(button);
    expect(annRow.getByLabelText("Your password")).toHaveFocus();
    await userEvent.click(annRow.getByRole("button", { name: "Cancel" }));
    expect(annRow.getByRole("button", { name: "Reset link" })).toHaveFocus();
  });
});

async function findRow(email: string) {
  await screen.findByText(email);
  return row(email);
}
