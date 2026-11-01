import { expect, test, type Page } from "@playwright/test";

// Screenshot tool for the README. Claims are real dataset claims
// replayed through the recorded provider, so every value shown is a
// saved Claude output, not mock data. Dataset files live outside the
// frontend directory, so paths are relative to it.
const FILES = "../evals/data/files";

async function uploadClaim(page: Page, filenames: string[]) {
  await page.goto("/");
  await page
    .getByLabel("Claim documents (JPG, PNG, PDF)")
    .setInputFiles(filenames.map((name) => `${FILES}/${name}`));
  await page.getByRole("button", { name: "Create claim and upload" }).click();
  await expect(page.getByRole("heading", { name: /^Claim / })).toBeVisible();
}

async function extract(page: Page) {
  await page.getByRole("button", { name: "Run extraction" }).click();
  await expect(page.getByRole("button", { name: "Run triage" })).toBeEnabled({
    timeout: 15000,
  });
}

async function triageTo(page: Page, route: string) {
  await page.getByRole("button", { name: "Run triage" }).click();
  await expect(page.getByRole("status", { name: route })).toBeVisible();
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: `../docs/screenshots/${name}`, fullPage: true });
}

test("ready claim", async ({ page }) => {
  await uploadClaim(page, ["doc_3292daab.pdf", "doc_aa484986.pdf", "doc_50e15da5.jpg"]);
  await extract(page);
  await triageTo(page, "Ready for adjuster");
  await shot(page, "ready-claim.png");
});

test("needs-more-info photo mismatch", async ({ page }) => {
  await uploadClaim(page, ["doc_c1ff9427.pdf", "doc_a7c24c68.jpg"]);
  await extract(page);
  await triageTo(page, "Needs more info");
  await shot(page, "needs-more-info-mismatch.png");
});

test("coverage-review claim", async ({ page }) => {
  // claim-0013: theft is excluded under its policy, and the recorded
  // triage carries that single reason with no consistency flag, so the
  // shot shows a coverage decision without any generator artifact.
  await uploadClaim(page, ["doc_ef523927.pdf", "doc_af1f184b.pdf", "doc_907ea1e1.jpg"]);
  await extract(page);
  await triageTo(page, "Likely not covered");
  await shot(page, "coverage-review.png");
});

test("document preview with a scanned page", async ({ page }) => {
  // claim-0084: a scanned water-damage form (burst pipe upstairs in a
  // townhouse) whose recorded triage is ready, so nothing in the shot
  // traces to a generator artifact.
  await uploadClaim(page, ["doc_0fe96474.pdf", "doc_192dc555.pdf", "doc_3def308c.jpg"]);
  await extract(page);
  // The file also appears as a source button on field rows, so take
  // the first match: the Files section precedes Extraction in the DOM.
  await page.getByRole("button", { name: "doc_0fe96474.pdf" }).first().click();
  await expect(page.getByRole("heading", { name: "doc_0fe96474.pdf" })).toBeVisible();
  // The PDF viewer needs a moment to fetch and rasterize the scanned
  // page before the iframe shows anything.
  await page.waitForTimeout(3000);
  // Viewport shot, not fullPage: Playwright's full-page capture resizes
  // the viewport while capturing, and Chrome's PDF viewer re-rasterizes
  // at a wrong zoom, rendering the page tiny and clipped. A tall
  // viewport fits the whole review page without resizing mid-capture.
  await page.setViewportSize({ width: 1280, height: 1700 });
  await page.screenshot({ path: "../docs/screenshots/document-preview-scanned.png" });
});

test("adjuster edit", async ({ page }) => {
  // claim-0007 is an amount-conflict claim: the model took the form's
  // figure ($48,175.17) and the repair estimate totals $59,070.14, so
  // the adjuster corrects the amount to the estimate's figure instead
  // of a round number with no document behind it.
  await uploadClaim(page, ["doc_f00ea29d.pdf", "doc_22c78ae8.pdf", "doc_b514db32.jpg"]);
  await extract(page);
  await page.getByLabel("Estimated damage (USD)").fill("59070.14");
  await page.getByRole("button", { name: "Save edits" }).click();
  await expect(page.getByRole("heading", { name: "Adjuster edits" })).toBeVisible();
  await shot(page, "adjuster-edit.png");
});

test("stale-triage banner", async ({ page }) => {
  // claim-0005 is an amount-conflict claim: the model took the form's
  // figure ($63,235.64) and the repair estimate totals $87,952.47, so
  // the adjuster corrects the amount to the estimate's figure.
  await uploadClaim(page, ["doc_8400bf6e.pdf", "doc_5ff1e393.pdf", "doc_14572b45.jpg"]);
  await extract(page);
  await triageTo(page, "Needs more info");
  await page.getByLabel("Estimated damage (USD)").fill("87952.47");
  await page.getByRole("button", { name: "Save edits" }).click();
  await expect(
    page.getByRole("status", { name: /Triage is out of date/ }),
  ).toBeVisible();
  await shot(page, "stale-triage-banner.png");
});
