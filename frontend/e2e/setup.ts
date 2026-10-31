/**
 * Test data for the e2e test, made through the API (no browser): log in as the seeded
 * admin, add a fresh reviewer, and start a run through the n8n webhook with one lead in
 * the request itself, so nothing is collected from OSM or Google. The values match
 * docker-compose.e2e.yml, which seeds that admin and sets that webhook secret.
 */
import { type APIRequestContext, expect } from "@playwright/test";

const ADMIN_EMAIL = "e2e-admin@example.com";
const ADMIN_PASSWORD = "e2e-admin-password";
const WEBHOOK_SECRET = "e2e-only-webhook-secret-not-used-anywhere-else";

export type Seeded = {
  reviewerEmail: string;
  reviewerPassword: string;
  leadName: string;
  runId: number;
  runItemId: number;
};

/** A reviewer + one lead waiting in the approval queue. Unique names, so reruns work. */
export async function seedWaitingLead(request: APIRequestContext): Promise<Seeded> {
  const stamp = Date.now();
  const reviewerEmail = `e2e-reviewer-${stamp}@example.com`;
  const reviewerPassword = `e2e-reviewer-${stamp}`;
  const leadName = `E2E Smile Dental ${stamp}`;

  const token = await logIn(request, ADMIN_EMAIL, ADMIN_PASSWORD);
  const auth = { Authorization: `Bearer ${token}` };

  const user = await request.post("/api/users", {
    headers: auth,
    data: { email: reviewerEmail, password: reviewerPassword, role: "reviewer" },
  });
  expect(user.status(), await user.text()).toBe(201);

  const started = await request.post("/api/webhooks/n8n/runs", {
    headers: { "X-Webhook-Secret": WEBHOOK_SECRET },
    data: {
      niche: "dentist",
      location: "Austin, TX",
      leads: [{ name: leadName, address: "1200 Congress Ave, Austin, TX 78701" }],
    },
  });
  expect(started.status(), await started.text()).toBe(202);
  const { run_id: runId } = (await started.json()) as { run_id: number };

  // The worker collects, enriches, extracts, checks and scores, then stops at the gate.
  let runItemId = 0;
  await expect
    .poll(
      async () => {
        const run = await request.get(`/api/runs/${runId}`, { headers: auth });
        const body = (await run.json()) as { items: { id: number; status: string }[] };
        runItemId = body.items[0]?.id ?? 0;
        return body.items[0]?.status;
      },
      { timeout: 60_000, message: `run ${runId} never reached the approval gate` },
    )
    .toBe("awaiting_approval");

  return { reviewerEmail, reviewerPassword, leadName, runId, runItemId };
}

async function logIn(request: APIRequestContext, email: string, password: string) {
  const response = await request.post("/api/auth/login", {
    form: { username: email, password },
  });
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { access_token: string }).access_token;
}
