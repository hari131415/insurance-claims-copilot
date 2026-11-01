import { expect, test, type Page } from "@playwright/test";

// Selectors use getByLabel/getByRole only. The mock provider returns
// fixed values (claimant Alex Rivera, policy POL-100001, amount 4500),
// and filenames drive its loss-type inference: car-damage.jpg reads
// as auto, which needs a repair estimate and damage photos, so the
// happy-path claim triages ready.
const FIXTURES = {
  form: "e2e/fixtures/claim-form.png",
  estimate: "e2e/fixtures/repair-estimate.png",
  photo: "e2e/fixtures/car-damage.jpg",
  invalid: "e2e/fixtures/invalid.txt",
};

async function createClaimWithFiles(page: Page, files: string[]) {
  await page.goto("/");
  await page.getByLabel("Claim documents (JPG, PNG, PDF)").setInputFiles(files);
  await page.getByRole("button", { name: "Create claim and upload" }).click();
  await expect(page.getByRole("heading", { name: /^Claim / })).toBeVisible();
}

async function extractAndWait(page: Page) {
  await page.getByRole("button", { name: "Run extraction" }).click();
  await expect(page.getByLabel("Claimant name")).toHaveValue("Alex Rivera", {
    timeout: 15000,
  });
}

test("upload, extract, and triage a claim end to end", async ({ page }) => {
  await createClaimWithFiles(page, [FIXTURES.form, FIXTURES.estimate, FIXTURES.photo]);
  await extractAndWait(page);
  await expect(page.getByLabel("Policy number")).toHaveValue("POL-100001");
  await expect(page.getByLabel("Estimated damage (USD)")).toHaveValue("4500");
  await page.getByRole("button", { name: "Run triage" }).click();
  await expect(page.getByRole("status", { name: "Ready for adjuster" })).toBeVisible();
});

test("adjuster edit marks triage stale and re-triage re-routes", async ({ page }) => {
  await createClaimWithFiles(page, [FIXTURES.form, FIXTURES.estimate, FIXTURES.photo]);
  await extractAndWait(page);
  await page.getByRole("button", { name: "Run triage" }).click();
  await expect(page.getByRole("status", { name: "Ready for adjuster" })).toBeVisible();
  // 100 is below the POL-100001 deductible of 500, so re-triage must
  // route to the coverage-review queue instead of ready.
  await page.getByLabel("Estimated damage (USD)").fill("100");
  await page.getByRole("button", { name: "Save edits" }).click();
  await expect(
    page.getByRole("status", { name: /Triage is out of date/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Re-run triage" }).click();
  await expect(page.getByRole("status", { name: "Likely not covered" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Set by adjuster" })).toBeVisible();
});

test("invalid upload shows an error and a retry link", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Claim documents (JPG, PNG, PDF)").setInputFiles([FIXTURES.invalid]);
  await page.getByRole("button", { name: "Create claim and upload" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("link", { name: "open it to retry" }).click();
  await expect(page.getByRole("heading", { name: /^Claim / })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run extraction" })).toBeDisabled();
});

test("unknown claim id shows an error and a way home", async ({ page }) => {
  await page.goto("/claims/does-not-exist");
  // 4xx responses are not retried, so the error surfaces immediately.
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("link", { name: "Back home" }).click();
  await expect(page.getByRole("heading", { name: "New claim" })).toBeVisible();
});
