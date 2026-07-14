import { expect, test } from "@playwright/test";

// Drives the seeded "HVAC — Sydney demo" run end-to-end through the real UI.
// The API is scripts/serve_demo_ui.py (12 companies, offline NL parser).

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("landing shows composer, suggestions and saved run", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "What are you looking to buy?" })).toBeVisible();
  await expect(page.getByPlaceholder("Describe your buy box…")).toBeVisible();
  await expect(page.getByRole("button", { name: "+ New search" })).toBeVisible();
  // Seeded run appears in the sidebar.
  await expect(page.getByText("HVAC — Sydney demo")).toBeVisible();
  await expect(page.getByText("· 12 companies")).toBeVisible();
});

test("opening the saved run restores conversation, trace and shortlist", async ({ page }) => {
  await page.getByText("HVAC — Sydney demo").click();

  // Conversation restored.
  await expect(
    page.getByText("Founder-owned HVAC installers in Sydney, $1–5M EBITDA"),
  ).toBeVisible();

  // Run trace with coverage detail.
  await expect(page.getByText("Sourcing run")).toBeVisible();
  await expect(page.getByText("420 raw → 280 after dedup")).toBeVisible();
  await expect(page.getByText("96 matched to an ABN")).toBeVisible();

  // Shortlist header + all 12 rows.
  await expect(page.getByRole("heading", { name: /Ranked shortlist/ })).toContainText(
    "12 companies",
  );
  const table = page.locator("table").last();
  await expect(table.getByText("Cool Breeze HVAC")).toBeVisible();

  // Gauges: every row has final / stat / evid / judge meters (label text is
  // lowercase in the DOM; CSS uppercases it). 4 per row × 12 rows.
  await expect(page.getByText("final", { exact: true })).toHaveCount(12);
  await expect(page.getByText("evid", { exact: true })).toHaveCount(12);
});

test("website links are real, external anchors", async ({ page }) => {
  await page.getByText("HVAC — Sydney demo").click();
  const link = page.getByRole("link", { name: "coolbreezehvac.com.au" });
  await expect(link).toHaveAttribute("href", "https://coolbreezehvac.com.au");
  await expect(link).toHaveAttribute("target", "_blank");
});

test("filter button 'Highest EBITDA' re-sorts and drops unknown-EBITDA rows", async ({ page }) => {
  await page.getByText("HVAC — Sydney demo").click();
  await page.getByRole("button", { name: "Highest EBITDA" }).click();

  // 3 of 12 have null EBITDA → filtered out.
  await expect(page.getByRole("heading", { name: /Ranked shortlist/ })).toContainText("9 companies");

  // First data row is now the highest EBITDA company (Coastal Air Solutions, $5.6M).
  const firstRow = page.locator("tbody tr").first();
  await expect(firstRow).toContainText("Coastal Air Solutions");
  await expect(firstRow).toContainText("$5.6M");
});

test("filter button 'Gov contracts' filters to gov-revenue companies", async ({ page }) => {
  await page.getByText("HVAC — Sydney demo").click();
  await page.getByRole("button", { name: "Gov contracts", exact: true }).click();
  // 4 seeded companies (every 3rd) carry gov contracts.
  await expect(page.getByRole("heading", { name: /Ranked shortlist/ })).toContainText("4 companies");
});

test("natural-language Refine re-ranks via the query endpoint", async ({ page }) => {
  await page.getByText("HVAC — Sydney demo").click();
  await page.getByPlaceholder(/Refine in words/).fill("only ones with award finalists");
  await page.getByRole("button", { name: "Refine" }).click();

  // Offline parser maps this to award_finalist=true → 3 companies.
  await expect(page.getByRole("heading", { name: /Ranked shortlist/ })).toContainText("3 companies");
  await expect(page.getByRole("button", { name: "Reset" })).toBeVisible();

  // Reset restores the full list.
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(page.getByRole("heading", { name: /Ranked shortlist/ })).toContainText("12 companies");
});

test("detail drawer opens inline with rationale, signals and provenance", async ({ page }) => {
  await page.getByText("HVAC — Sydney demo").click();
  const row = page.locator("tbody tr", { hasText: "Cool Breeze HVAC" }).first();
  await row.getByText("Cool Breeze HVAC").click();

  await expect(page.getByText(/ABN 10000000000/)).toBeVisible();
  await expect(page.getByText(/Strong sector and geography fit/)).toBeVisible();
  await expect(page.getByText("Open diligence")).toBeVisible();

  // Provenance table loaded from GET /companies/{abn}/sources.
  await expect(page.getByText("Provenance")).toBeVisible();
  await expect(page.getByText("abn_lookup_api")).toBeVisible();
  await expect(page.getByText("website_fetch")).toBeVisible();

  // Save to list works.
  await page.getByRole("button", { name: "Save to list" }).click();
  await expect(page.getByRole("button", { name: "✓ Saved to list" })).toBeVisible();
});
