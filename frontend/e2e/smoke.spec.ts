import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

import { expect, test, type Page } from "@playwright/test";

// One walk through the product as the demo manager, in a real browser: sign in, see what needs
// attention, open the machine, see today's work, read the analysis. Then the same screens at phone
// width, and a real-browser accessibility audit (including colour contrast, which unit tests cannot do).

const require = createRequire(import.meta.url);

// The demo's backfilled telemetry is hours old unless the live simulator is running, which would turn
// every machine into "No recent data". These walkthroughs are about everything else, so every asset is
// reported as having just sent a reading; e2e/stale.spec.ts covers the quiet case.
async function reportEveryAssetAsLive(page: Page): Promise<void> {
  await page.route("**/telemetry-freshness", async (route) => {
    try {
      const real = (await (await route.fetch()).json()) as {
        as_of: string;
        assets: { asset_id: string }[];
      };
      const justNow = new Date(Date.parse(real.as_of) - 5000).toISOString();
      await route.fulfill({
        json: {
          as_of: real.as_of,
          assets: real.assets.map((a) => ({ asset_id: a.asset_id, last_reading_at: justNow })),
        },
      });
    } catch {
      // The test ended while a 30 s poll was in flight: nothing left to answer.
    }
  });
}

async function signInAsDemoManager(page: Page): Promise<void> {
  await reportEveryAssetAsLive(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Use demo account" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Your facilities" })).toBeVisible();
}

async function openMeridian(page: Page): Promise<void> {
  const card = page.getByRole("article", { name: "Meridian Components Plant" });
  await card.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
}

test.describe("manager walkthrough (desktop)", () => {
  test("sign in, spot the problem machine, open it, see today's work and the analysis", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);

    // Overview: the failing machine is called out first, with why and what to do.
    const attention = page.getByRole("region", { name: "Needs attention" });
    const first = attention.getByRole("listitem").first();
    await expect(first).toContainText("Air Compressor AC-1");
    await expect(first).toContainText("Failing soon");
    await expect(first).toContainText("Schedule maintenance now");

    // Details: the Assets tab opens with that machine's panel and its risk trend.
    await first.getByRole("link", { name: "Details" }).click();
    const panel = page.getByRole("complementary", { name: "Details for Air Compressor AC-1" });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("heading", { name: "Risk trend" })).toBeVisible();
    await expect(page).not.toHaveURL(/asset=/); // the deep-link param cleans itself up

    // Work: the critical job is today's stop, and the rest is scheduled for later (not "unassigned").
    await page.getByRole("link", { name: "Work", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Work" })).toBeVisible();
    await expect(page.getByRole("listitem", { name: /Stop 1: Air Compressor AC-1/ })).toBeVisible();
    await expect(page.getByRole("region", { name: "Scheduled for later" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Not assigned" })).toHaveCount(0);

    // Analysis: the answer comes first and validation status is visible.
    await page.getByRole("link", { name: "Analysis", exact: true }).click();
    await expect(page.getByRole("region", { name: "Answer" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Validation errors" })).toBeVisible();
  });

  test("the floor filter and quick filters narrow the asset list and live in the URL", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);
    await page.getByRole("link", { name: "Assets", exact: true }).click();

    await page.getByRole("button", { name: /Ground floor/ }).click();
    await expect(page).toHaveURL(/floor=0/);
    await page.getByRole("button", { name: "Needs attention" }).click();
    await expect(page).toHaveURL(/show=attention/);
    await expect(page.getByRole("table", { name: "Asset health" }).getByRole("row")).toHaveCount(4); // header + 3
  });

  test("an old /results link still lands somewhere useful", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);
    const overviewUrl = page.url();
    await page.goto(overviewUrl.replace("/overview", "/results"));
    await expect(page).toHaveURL(/\/overview$/);
  });
});

test.describe("keyboard", () => {
  test("the first Tab stop is a skip link that jumps past the navigation", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);
    // a fresh page load, as a keyboard user arriving on the page: the very first Tab stop
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to main content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content")).toBeFocused();
  });

  test("the risk trend chart is one tab stop and reads out each score with the arrow keys", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);
    await page.getByRole("region", { name: "Needs attention" }).getByRole("link", { name: "Details" }).first().click();
    const chart = page.getByRole("group", { name: /Risk over the last \d+ scores/ });
    await chart.scrollIntoViewIfNeeded();
    await chart.focus();
    const live = page.getByTestId("chart-live");
    await expect(live).toContainText("risk");
    const latest = await live.textContent();
    await page.keyboard.press("ArrowLeft");
    await expect(live).not.toHaveText(latest ?? "");
    await page.keyboard.press("Escape");
    await expect(live).toBeEmpty();
  });
});

test.describe("phone width (390 x 844)", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  async function noSidewaysScroll(page: Page): Promise<void> {
    const overflow = await page.evaluate(() => {
      const main = document.querySelector(".app-main") ?? document.documentElement;
      return {
        doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        main: main.scrollWidth - main.clientWidth,
      };
    });
    expect(overflow.doc).toBeLessThanOrEqual(0);
    expect(overflow.main).toBeLessThanOrEqual(0);
  }

  test("every tab fits on the screen and every control is reachable", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);

    // the top bar keeps tabs, About and Sign out on screen
    for (const name of ["Overview", "Map", "Assets", "Work", "Analysis"]) {
      await expect(page.getByRole("navigation", { name: "Facility" }).getByText(name, { exact: true })).toBeVisible();
    }
    await expect(page.getByRole("link", { name: "About the models" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await noSidewaysScroll(page);

    for (const tab of ["Assets", "Work", "Analysis", "Map"]) {
      await page.getByRole("link", { name: tab, exact: true }).click();
      await expect(page.getByRole("navigation", { name: "Facility" })).toBeVisible();
      await page.waitForTimeout(1500);
      await noSidewaysScroll(page);
    }
  });

  test("tapping a machine opens a details sheet you can close", async ({ page }) => {
    await signInAsDemoManager(page);
    await openMeridian(page);
    await page.getByRole("link", { name: "Assets", exact: true }).click();
    await page.getByRole("button", { name: "Air Compressor AC-1" }).click();
    const sheet = page.getByRole("complementary", { name: "Details for Air Compressor AC-1" });
    await expect(sheet).toBeVisible();
    await sheet.getByRole("button", { name: "Close" }).click();
    await expect(sheet).toHaveCount(0);
  });
});

test.describe("accessibility audit in a real browser (axe, including colour contrast)", () => {
  const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf-8");

  async function audit(page: Page): Promise<string[]> {
    await page.evaluate(axeSource);
    return page.evaluate(async () => {
      // @ts-expect-error axe is injected by the line above
      const results = await window.axe.run(document, { resultTypes: ["violations"] });
      return results.violations.flatMap((v: { id: string; impact: string; nodes: { target: string[] }[] }) =>
        v.nodes.map((n) => `${v.id} (${v.impact}): ${n.target.join(" ")}`)
      );
    });
  }

  test("no violations on the main screens", async ({ page }) => {
    await signInAsDemoManager(page);
    expect(await audit(page)).toEqual([]);
    await openMeridian(page);
    expect(await audit(page)).toEqual([]);
    for (const tab of ["Assets", "Work", "Analysis"]) {
      await page.getByRole("link", { name: tab, exact: true }).click();
      await expect(page.getByRole("heading", { level: 1, name: tab })).toBeVisible();
      await page.waitForTimeout(1500);
      expect(await audit(page), tab).toEqual([]);
    }

    // the map (its canvas and controls are third-party markup) and the About page
    await page.getByRole("link", { name: "Map", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Floor map" })).toBeAttached();
    await page.waitForTimeout(4000);
    expect(await audit(page), "Map").toEqual([]);

    await page.getByRole("link", { name: "About the models" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "About the models" })).toBeVisible();
    expect(await audit(page), "About").toEqual([]);
  });
});
