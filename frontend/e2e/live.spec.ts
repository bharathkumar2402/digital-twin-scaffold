import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

// The live UI (Phase L task L.6) in a real browser. The WebSocket is played by the test, so a push can
// be sent at an exact moment (and the connection refused or dropped on demand) without depending on
// the simulator, the worker or the clock; the REST data still comes from the real API.

type Score = { asset_id: string; score: number; [key: string]: unknown };

let sockets: WebSocketRoute[] = [];
let refuseSockets = false;
let bumpScores = false;

async function arrange(page: Page): Promise<void> {
  sockets = [];
  refuseSockets = false;
  bumpScores = false;

  await page.routeWebSocket(/\/ws\/alerts/, (ws) => {
    if (refuseSockets) {
      void ws.close();
      return;
    }
    sockets.push(ws);
  });

  // Every machine has "just reported", so this test is about scores, not about quiet sensors.
  await page.route("**/telemetry-freshness", async (route) => {
    try {
      const real = (await (await route.fetch()).json()) as { as_of: string; assets: { asset_id: string }[] };
      const justNow = new Date(Date.parse(real.as_of) - 5000).toISOString();
      await route.fulfill({
        json: {
          as_of: real.as_of,
          assets: real.assets.map((a) => ({ asset_id: a.asset_id, last_reading_at: justNow })),
        },
      });
    } catch {
      // the test ended with a poll in flight
    }
  });

  // After a push the next scores answer is changed, so a screen that refetched is visibly different:
  // the lowest-risk machine suddenly scores 99 (one more "high risk").
  await page.route("**/risk-scores", async (route) => {
    try {
      const response = await route.fetch();
      const scores = (await response.json()) as Score[];
      if (bumpScores && scores.length > 0) {
        const lowest = scores.reduce((a, b) => (b.score < a.score ? b : a));
        lowest.score = 99;
      }
      await route.fulfill({ response, json: scores });
    } catch {
      // the test ended with a poll in flight
    }
  });
}

async function openOverview(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Use demo account" }).click();
  const card = page.getByRole("article", { name: "Meridian Components Plant" });
  await card.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  return /facilities\/([^/]+)\//.exec(page.url())![1]!;
}

const highRisk = (page: Page) => page.locator(".kpi").filter({ hasText: "High risk" }).locator(".kpi-value");

test.describe("live updates", () => {
  test("a pushed score update refreshes Needs attention and the KPIs without a reload", async ({ page }) => {
    await arrange(page);
    const facilityId = await openOverview(page);
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();

    const before = Number(await highRisk(page).innerText());

    bumpScores = true;
    sockets[0]!.send(
      JSON.stringify({
        type: "scores_updated",
        facility_id: facilityId,
        scored: 44,
        computed_at: new Date().toISOString(),
      })
    );

    await expect(highRisk(page)).toHaveText(String(before + 1)); // no reload, no click
  });

  test("a push for a different facility changes nothing on this screen", async ({ page }) => {
    await arrange(page);
    await openOverview(page);
    const before = Number(await highRisk(page).innerText());

    bumpScores = true;
    sockets[0]!.send(
      JSON.stringify({
        type: "scores_updated",
        facility_id: "00000000-0000-4000-8000-0000000000ff",
        scored: 44,
        computed_at: new Date().toISOString(),
      })
    );
    await page.waitForTimeout(1500); // longer than the debounce

    await expect(highRisk(page)).toHaveText(String(before));
  });

  test("an anomaly alert pops up on the Overview tab, not only on the map", async ({ page }) => {
    await arrange(page);
    await openOverview(page);

    sockets[0]!.send(
      JSON.stringify({
        asset_id: "d3500000-0000-4000-8000-0000000000aa",
        sensor_type: "vibration",
        value: 123.4,
        z_score: 9.9,
        triggered_at: new Date().toISOString(),
      })
    );

    await expect(page.getByRole("alert").filter({ hasText: "Anomaly detected" })).toBeVisible();
  });

  test("a hostile or unknown frame is ignored and the page keeps working", async ({ page }) => {
    await arrange(page);
    await openOverview(page);

    sockets[0]!.send("{not json");
    sockets[0]!.send(JSON.stringify({ type: "something_new", facility_id: 7 }));
    sockets[0]!.send(JSON.stringify({ type: "scores_updated", facility_id: { $ne: null } }));

    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
  });

  test("when the connection drops the screen says it is polling, and says Live again once it is back", async ({
    page,
  }) => {
    await arrange(page);
    await openOverview(page);
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();

    refuseSockets = true;
    await sockets[0]!.close();
    await expect(
      page.getByRole("status").filter({ hasText: "Reconnecting, refreshing every minute" })
    ).toBeVisible();

    refuseSockets = false; // the page retries every 2 s
    await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible({ timeout: 15_000 });
  });
});
