import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// The Phase L loop against the REAL stack, nothing mocked: simulator -> API -> scheduled scoring (beat +
// worker) -> WebSocket -> screen, plus the automatic analysis. It is skipped unless E2E_LIVE=1 because it
// needs `scripts\demo_up.ps1` running and takes minutes of wall-clock time:
//
//   .\scripts\demo_up.ps1 -StoryDelayMin 0.5 -StoryMinutes 3
//   cd frontend; $env:E2E_LIVE = "1"; npx playwright test live-stack
//
// Start the stack fresh (or with the storyline not yet finished): the storyline runs once per simulator
// start. Measured times are printed so the budget is on record, not assumed.

const live = process.env.E2E_LIVE === "1";
const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const tenantId = "d3500000-0000-4000-8000-000000000001";
const backendDir = path.resolve(process.cwd(), "..", "backend");
const python = process.env.E2E_PYTHON ?? path.join(backendDir, ".venv", "Scripts", "python.exe");
const run = promisify(execFile);

const STORYLINE_BUDGET_MS = 8 * 60_000; // delay + climb + one scoring pass + one refresh
const STALE_BUDGET_MS = 7 * 60_000; // 5 min of silence + one scoring/refresh interval
const RECOVERY_BUDGET_MS = 3 * 60_000;

type AgentRun = { trigger: string; status: string; created_at: string };

async function signIn(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Use demo account" }).click();
  const card = page.getByRole("article", { name: "Meridian Components Plant" });
  await card.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  return /facilities\/([^/]+)\//.exec(page.url())![1]!;
}

async function apiToken(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${apiBase}/login`, {
    data: { tenant_id: tenantId, email: "demo@twinlabs.com", password: "demo-password-123" },
  });
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as { access_token: string }).access_token;
}

async function agentRuns(request: APIRequestContext, facilityId: string): Promise<AgentRun[]> {
  const response = await request.get(`${apiBase}/facilities/${facilityId}/agent-runs?limit=50`, {
    headers: { Authorization: `Bearer ${await apiToken(request)}` },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as AgentRun[];
}

function simulator(...args: string[]): Promise<unknown> {
  return run(python, ["-m", "scripts.live_simulator", ...args], { cwd: backendDir });
}

test.describe("live loop on the real stack", () => {
  test.skip(!live, "set E2E_LIVE=1 with scripts\\demo_up.ps1 running");

  test("the storyline machine reaches Needs attention by itself, with a toast and an automatic analysis", async ({
    page,
    request,
  }) => {
    test.setTimeout(STORYLINE_BUDGET_MS + 4 * 60_000);
    const started = Date.now();
    const facilityId = await signIn(page);

    // The toast can come well before AC-2 is listed, so watch for it from the start.
    const toast = page
      .getByRole("alert")
      .filter({ hasText: "Anomaly detected" })
      .first()
      .waitFor({ timeout: STORYLINE_BUDGET_MS })
      .then(
        () => true,
        () => false
      );

    // No reload, no click: the list has to change on its own.
    const attention = page.getByRole("region", { name: "Needs attention" });
    await expect(attention.getByRole("listitem").filter({ hasText: "AC-2" }).first()).toBeVisible({
      timeout: STORYLINE_BUDGET_MS,
    });
    const listedAfter = Math.round((Date.now() - started) / 1000);

    expect(await toast, "an anomaly toast should have appeared while AC-2 degraded").toBe(true);

    // The automatic analysis is queued by the scoring pass and runs on the worker; it may take a moment.
    await expect
      .poll(
        async () => (await agentRuns(request, facilityId)).some((r) => r.trigger.startsWith("auto_")),
        { timeout: 4 * 60_000, intervals: [5_000] }
      )
      .toBe(true);
    const autoRuns = (await agentRuns(request, facilityId)).filter((r) => r.trigger.startsWith("auto_"));

    console.log(
      `[live] AC-2 in Needs attention after ${listedAfter}s; automatic runs: ` +
        autoRuns.map((r) => `${r.trigger}=${r.status}`).join(", ")
    );
    // A run is recorded even when the model call fails (no API key); the point is that it happened once,
    // not that the LLM answered. At most one per 10 minutes per facility (rule 4).
    const recent = autoRuns.filter((r) => Date.now() - Date.parse(r.created_at) < 10 * 60_000);
    expect(recent.length).toBeLessThanOrEqual(2); // one start-up run and one high-risk run at most
  });

  test("a machine that goes quiet shows No recent data by itself, and recovers when it resumes", async ({
    page,
  }) => {
    test.setTimeout(STALE_BUDGET_MS + RECOVERY_BUDGET_MS + 2 * 60_000);
    await signIn(page);
    await page.getByRole("link", { name: "Assets", exact: true }).click();
    const table = page.getByRole("table", { name: "Asset health" });
    const row = table.getByRole("row").filter({ hasText: "PB-1" }).first();
    await expect(row).toBeVisible();
    await expect(row).not.toContainText("No recent data"); // it is reporting

    await simulator("inject", "PB-1", "sensor_dropout", "--duration-s", "900");
    const silentSince = Date.now();
    try {
      await expect(row).toContainText("No recent data", { timeout: STALE_BUDGET_MS });
      console.log(`[live] PB-1 showed No recent data after ${Math.round((Date.now() - silentSince) / 1000)}s`);
    } finally {
      await simulator("clear", "PB-1");
    }

    const cleared = Date.now();
    await expect(row).not.toContainText("No recent data", { timeout: RECOVERY_BUDGET_MS });
    console.log(`[live] PB-1 recovered after ${Math.round((Date.now() - cleared) / 1000)}s`);
  });
});
