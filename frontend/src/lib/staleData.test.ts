import { describe, expect, it } from "vitest";

import { applyQuickFilter } from "./assetFilters";
import { buildHealthRows, isStale, STALE_AFTER_MS, summarizeHealth } from "./assetHealth";
import { assetStatus, formatAge } from "./assetStatus";
import { topAttention } from "./attention";
import type { Asset, RiskScore, TelemetryFreshness } from "../types/api";

const AS_OF = "2026-10-12T12:00:00Z";
const asOfMs = Date.parse(AS_OF);

function asset(id: string): Asset {
  return {
    id,
    facility_id: "f1",
    name: `Machine ${id}`,
    type: "pump",
    floor_level: 0,
    x: 0,
    y: 0,
    status: "operational",
    installed_date: null,
    manufacturer: null,
    model: null,
  };
}

function score(assetId: string, value: number): RiskScore {
  return {
    id: `r-${assetId}`,
    facility_id: "f1",
    asset_id: assetId,
    score: value,
    rul_cycles: null,
    model_version: "v1",
    factors_json: {},
    computed_at: "2026-10-12T11:59:00Z",
  };
}

// Freshness saying asset "a" last reported `ageMs` before the server's clock (null = never).
function fresh(ageMs: number | null, id = "a"): TelemetryFreshness {
  return {
    as_of: AS_OF,
    assets: [
      {
        asset_id: id,
        last_reading_at: ageMs === null ? null : new Date(asOfMs - ageMs).toISOString(),
      },
    ],
  };
}

function row(ageMs: number | null, risk: number | null) {
  return buildHealthRows([asset("a")], risk === null ? [] : [score("a", risk)], fresh(ageMs))[0];
}

describe("stale data: the 5 minute boundary", () => {
  it("one millisecond under 5 minutes is still fresh, exactly 5 minutes is stale", () => {
    expect(STALE_AFTER_MS).toBe(300_000);
    expect(isStale(row(STALE_AFTER_MS - 1, 10))).toBe(false);
    expect(assetStatus(row(STALE_AFTER_MS - 1, 10)).key).toBe("healthy");
    expect(isStale(row(STALE_AFTER_MS, 10))).toBe(true);
    expect(assetStatus(row(STALE_AFTER_MS, 10)).key).toBe("stale");
  });

  it("is measured on the server's clock, so a wrong browser clock changes nothing", () => {
    const real = Date.now;
    Date.now = () => 0; // a browser whose clock is decades behind
    try {
      expect(assetStatus(row(STALE_AFTER_MS, 10)).key).toBe("stale");
      expect(assetStatus(row(1000, 10)).key).toBe("healthy");
    } finally {
      Date.now = real;
    }
  });

  it("a reading dated after the server clock counts as age 0, not negative", () => {
    const r = buildHealthRows([asset("a")], [], {
      as_of: AS_OF,
      assets: [{ asset_id: "a", last_reading_at: "2026-10-12T12:00:05Z" }],
    })[0];
    expect(r.readingAgeMs).toBe(0);
  });
});

describe("stale data: what a quiet asset is called", () => {
  it.each([0, 20, 50, 66.9])("risk %s with no recent data is never Healthy", (risk) => {
    const status = assetStatus(row(10 * 60_000, risk));
    expect(status.key).toBe("stale");
    expect(status.label).toBe("No recent data");
    expect(status.action).toMatch(/sensor/i);
    expect(status.reasons.join(" ")).toMatch(/no sensor reading for 10 min ago/);
  });

  it("a high stored risk still wins: a failing machine is not hidden by a quiet sensor", () => {
    const status = assetStatus(row(60 * 60_000, 80));
    expect(status.key).toBe("critical");
    expect(status.reasons.join(" ")).toMatch(/no sensor reading for 1 h ago/);
  });

  it("is a different state from unscored: stale + unscored is stale, fresh + unscored is unknown", () => {
    expect(assetStatus(row(10 * 60_000, null)).key).toBe("stale");
    const unscored = assetStatus(row(1000, null));
    expect(unscored.key).toBe("unknown");
    expect(unscored.label).toBe("Not enough data");
  });

  it("recovers when data resumes: the same asset with a fresh reading is Healthy again", () => {
    expect(assetStatus(row(30 * 60_000, 10)).key).toBe("stale");
    expect(assetStatus(row(15_000, 10)).key).toBe("healthy");
  });

  it("claims nothing when the freshness lookup has not answered or the asset is not in it", () => {
    const noLookup = buildHealthRows([asset("a")], [score("a", 10)])[0];
    expect(noLookup.readingAgeMs).toBeUndefined();
    expect(assetStatus(noLookup).key).toBe("healthy");
    const missing = buildHealthRows([asset("a")], [score("a", 10)], fresh(1000, "other"))[0];
    expect(missing.readingAgeMs).toBeUndefined();
    expect(assetStatus(missing).key).toBe("healthy");
  });

  it("an asset that never reported has no feed to lose: it keeps its score-based status", () => {
    const never = row(null, 10);
    expect(never.readingAgeMs).toBeNull();
    expect(isStale(never)).toBe(false);
    expect(assetStatus(never).key).toBe("healthy");
  });

  it("an unparseable timestamp from the API is not turned into a stale claim", () => {
    const r = buildHealthRows([asset("a")], [score("a", 10)], {
      as_of: "not a date",
      assets: [{ asset_id: "a", last_reading_at: "2026-10-12T11:00:00Z" }],
    })[0];
    expect(isStale(r)).toBe(false);
  });
});

describe("stale data: where it shows up", () => {
  const assets = [asset("a"), asset("b"), asset("c")];
  const freshness: TelemetryFreshness = {
    as_of: AS_OF,
    assets: [
      { asset_id: "a", last_reading_at: "2026-10-12T11:00:00Z" }, // 1 h old
      { asset_id: "b", last_reading_at: "2026-10-12T11:59:50Z" },
      { asset_id: "c", last_reading_at: "2026-10-12T11:00:00Z" },
    ],
  };
  const rows = buildHealthRows(assets, [score("a", 10), score("b", 10), score("c", 90)], freshness);

  it("the quiet low-risk machine joins Needs attention after the failing one; the live one stays out", () => {
    const { top, total } = topAttention(rows);
    expect(total).toBe(2);
    expect(top.map((r) => r.asset.id)).toEqual(["c", "a"]);
  });

  it("is counted in the summary (a failing machine can be quiet too) and in the Needs attention filter", () => {
    expect(summarizeHealth(rows).stale).toBe(2); // a and c; b reported 10 s ago
    expect(
      applyQuickFilter(rows, "attention")
        .map((r) => r.asset.id)
        .sort()
    ).toEqual(["a", "c"]);
  });

  it("ranks between warning and unknown", () => {
    const stale = assetStatus(rows.find((r) => r.asset.id === "a")!).rank;
    const warning = assetStatus(buildHealthRows([asset("w")], [score("w", 50)])[0]).rank;
    const unknown = assetStatus(buildHealthRows([asset("u")], [])[0]).rank;
    expect(stale).toBeGreaterThan(warning);
    expect(stale).toBeLessThan(unknown);
  });
});

describe("formatAge", () => {
  it.each([
    [5 * 60_000, "5 min ago"],
    [59 * 60_000 + 59_000, "59 min ago"],
    [60 * 60_000, "1 h ago"],
    [47 * 3_600_000, "47 h ago"],
    [72 * 3_600_000, "3 days ago"],
  ])("%d ms -> %s", (ms, text) => {
    expect(formatAge(ms)).toBe(text);
  });
});
