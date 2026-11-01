import { expect, test } from "@playwright/test";

test("recorded provider shows the recorded badge", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("status", { name: "Demo mode – recorded Claude results" }),
  ).toBeVisible();
});

test("recorded provider fails clearly on unknown files", async ({ page }) => {
  // These fixtures are eval samples, not dataset files, so no
  // recording matches them.
  await page.goto("/");
  await page
    .getByLabel("Claim documents (JPG, PNG, PDF)")
    .setInputFiles(["e2e/fixtures/claim-form.png"]);
  await page.getByRole("button", { name: "Create claim and upload" }).click();
  await expect(page.getByRole("heading", { name: /^Claim / })).toBeVisible();
  await page.getByRole("button", { name: "Run extraction" }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 15000 });
  await expect(alert).toContainText("No recorded Claude output");
});
