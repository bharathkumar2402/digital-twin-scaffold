import { expect, test, type Page } from "@playwright/test";

// "No recent data": an asset that stopped reporting is never shown as Healthy. The freshness route is
// answered by the real API and then rewritten, so these tests control how old every asset's newest
// reading is without waiting five real minutes or depending on whether the simulator is running.

type Freshness = { as_of: string; assets: { asset_id: string; last_reading_at: string | null }[] };

let ageSeconds = 3600;

async function ageEveryAsset(page: Page): Promise<void> {
  await page.route("**/telemetry-freshness", async (route) => {
    try {
      const real = (await (await route.fetch()).json()) as Freshness;
      const asOf = Date.parse(real.as_of);
      const body: Freshness = {
        as_of: real.as_of,
        assets: real.assets.map((a) => ({
          asset_id: a.asset_id,
          last_reading_at: new Date(asOf - ageSeconds * 1000).toISOString(),
        })),
      };
      await route.fulfill({ json: body });
    } catch {
      // The test ended while a poll was in flight: nothing left to answer.
    }
  });
}

async function openOverview(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: "Use demo account" }).click();
  const card = page.getByRole("article", { name: "Meridian Components Plant" });
  await card.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
}

test.describe("no recent data", () => {
  test("a quiet machine is called out on Overview, Assets and Work, and a failing one stays failing", async ({
    page,
  }) => {
    ageSeconds = 3600;
    await ageEveryAsset(page);
    await openOverview(page);

    const attention = page.getByRole("region", { name: "Needs attention" });
    const first = attention.getByRole("listitem").first();
    await expect(first).toContainText("Failing soon"); // high risk wins over a quiet sensor
    await expect(first).toContainText("no sensor reading for 1 h ago");
    await expect(attention.getByText("No recent data").first()).toBeVisible();
    await expect(page.getByRole("group", { name: "Facility summary" })).toContainText("no recent data");
    await expect(page.getByText("All clear")).toHaveCount(0);

    await page.getByRole("link", { name: "Assets", exact: true }).click();
    const table = page.getByRole("table", { name: "Asset health" });
    await expect(table.getByText("No recent data").first()).toBeVisible();
    await expect(table.getByText("Healthy")).toHaveCount(0);

    await page.getByRole("link", { name: "Work", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Work" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "No recent data" })).toBeVisible();
  });

  test("fresh readings clear it, and it comes back when they stop", async ({ page }) => {
    ageSeconds = 20;
    await ageEveryAsset(page);
    await openOverview(page);
    const attention = page.getByRole("region", { name: "Needs attention" });
    await expect(attention.getByText("No recent data")).toHaveCount(0);
    await expect(page.getByRole("group", { name: "Facility summary" })).not.toContainText("no recent data");

    ageSeconds = 600; // 10 minutes of silence
    await page.reload();
    await expect(attention.getByText("No recent data").first()).toBeVisible();

    ageSeconds = 5; // data resumes
    await page.reload();
    await expect(attention.getByText("No recent data")).toHaveCount(0);
  });
});
