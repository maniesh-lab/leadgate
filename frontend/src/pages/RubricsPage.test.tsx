/** Rubric settings: an edit is saved as the next version, with the values typed right. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Rubric } from "../api/types";
import { ADMIN, fakeApi, page, renderApp, reply } from "../test/render";

const WEB_AGENCY: Rubric = {
  id: 5,
  name: "web-agency",
  version: 2,
  rules: [
    { label: "rating 4.0 or more", field: "rating", op: "gte", value: 4.0, points: 20 },
    { label: "no website", field: "website", op: "missing", value: null, points: 30 },
  ],
  created_by: 1,
  created_at: "2026-10-30T09:00:00Z",
};

describe("RubricsPage", () => {
  it("saves an edit as the next version", async () => {
    const bodies: unknown[] = [];
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page([WEB_AGENCY, { ...WEB_AGENCY, id: 3, version: 1 }]),
      "POST /api/rubrics/5": (_url, init) => {
        bodies.push(JSON.parse(String(init.body)));
        return reply(201, { ...WEB_AGENCY, id: 9, version: 3 });
      },
    });
    const router = renderApp("/settings/rubrics", { user: ADMIN });

    const points = await screen.findByLabelText("Rule 1 points");
    await userEvent.clear(points);
    await userEvent.type(points, "25");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await userEvent.type(screen.getByLabelText("Rule 3 label"), "category is dentist");
    await userEvent.type(screen.getByLabelText("Rule 3 field"), "category");
    await userEvent.selectOptions(screen.getByLabelText("Rule 3 op"), "in");
    await userEvent.type(screen.getByLabelText("Rule 3 value"), "dentist, orthodontist");
    await userEvent.type(screen.getByLabelText("Rule 3 points"), "10");
    await userEvent.click(screen.getByRole("button", { name: "Save as v3" }));

    await waitFor(() => expect(router.state.location.search).toBe("?id=9"));
    expect(bodies).toEqual([
      {
        rules: [
          {
            label: "rating 4.0 or more",
            field: "rating",
            op: "gte",
            value: 4,
            points: 25,
            required: false,
          },
          {
            label: "no website",
            field: "website",
            op: "missing",
            value: null,
            points: 30,
            required: false,
          },
          {
            label: "category is dentist",
            field: "category",
            op: "in",
            value: ["dentist", "orthodontist"],
            points: 10,
            required: false,
          },
        ],
      },
    ]);
  });

  it("shows the API's refusal", async () => {
    fakeApi({
      "GET /api/auth/me": () => ADMIN,
      "GET /api/rubrics": () => page([WEB_AGENCY]),
      "POST /api/rubrics/5": () =>
        reply(422, {
          error: {
            code: "validation_error",
            message: "invalid request",
            details: [{ loc: ["body", "rules", 0, "value"], msg: "op 'gte' needs a number" }],
          },
        }),
    });
    renderApp("/settings/rubrics", { user: ADMIN });

    await userEvent.click(await screen.findByRole("button", { name: "Save as v3" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "rules.0.value: op 'gte' needs a number",
    );
  });
});
