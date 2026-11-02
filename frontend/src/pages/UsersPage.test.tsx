/** The Users page: list, add a reviewer, turn an account off (asks first) or on. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "../api/types";
import { ADMIN, fakeApi, page, REVIEWER, renderApp, reply } from "../test/render";

const SAM: User = { ...REVIEWER, id: 3, email: "sam@agency.test" };
const OLD: User = { ...REVIEWER, id: 4, email: "old@agency.test", is_active: false };

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
