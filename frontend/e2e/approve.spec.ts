/**
 * The main flow in a real browser: a reviewer logs in, opens a waiting lead from the
 * approval queue, approves it with a reason; the worker finishes the item (route + act,
 * with the CRM and Discord faked by the e2e stack), and the audit log shows the decision.
 */
import { expect, test } from "@playwright/test";
import { seedWaitingLead } from "./setup";

test("a reviewer approves a lead and the audit log records it", async ({ page, request }) => {
  const seeded = await seedWaitingLead(request);

  await test.step("log in as the reviewer", async () => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await page.getByLabel("Email").fill(seeded.reviewerEmail);
    await page.getByLabel("Password").fill(seeded.reviewerPassword);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  });

  await test.step("approve the lead from the queue", async () => {
    await page.getByRole("link", { name: "Queue" }).click();
    const row = page.getByRole("row").filter({ hasText: seeded.leadName });
    await row.getByRole("button", { name: "Review" }).click();

    const dialog = page.getByRole("dialog", { name: seeded.leadName });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Reason").fill("e2e: a small practice, good fit");
    await dialog.getByRole("button", { name: /^Approve/ }).click();

    // Decided: the dialog moves on (or closes) and the lead leaves the queue.
    await expect(dialog).toBeHidden();
    await expect(row).toHaveCount(0);
  });

  await test.step("the worker finishes the item", async () => {
    await page.goto(`/run-items/${seeded.runItemId}`);
    await expect(page.getByRole("heading", { name: seeded.leadName })).toBeVisible();
    // The page doesn't refresh itself, so reload until the resume job has run.
    await expect(async () => {
      await page.reload();
      await expect(page.getByText("done", { exact: true })).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 60_000 });
  });

  await test.step("the audit log shows the approval", async () => {
    await page.getByRole("link", { name: "Audit" }).click();
    // A filter applies on Enter (or when the box loses focus).
    await page.getByLabel("Action", { exact: true }).fill("approval.approved");
    await page.getByLabel("Action", { exact: true }).press("Enter");
    await page.getByLabel("Run", { exact: true }).fill(String(seeded.runId));
    await page.getByLabel("Run", { exact: true }).press("Enter");
    const row = page.getByRole("row").filter({ hasText: seeded.reviewerEmail });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("approval.approved");
  });
});
