import { describe, expect, it } from "vitest";

import { buildHealthRows } from "./assetHealth";
import { applyQuickFilter, parseFloorParam, parseQuickFilter, searchRows } from "./assetFilters";
import type { Asset, RiskScore } from "../types/api";

const asset = (id: string, name: string, type = "pump"): Asset => ({
  id,
  facility_id: "f1",
  name,
  type,
  floor_level: 0,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
});

const score = (assetId: string, value: number, factors: RiskScore["factors_json"] = {}): RiskScore => ({
  id: `r-${assetId}`,
  facility_id: "f1",
  asset_id: assetId,
  score: value,
  rul_cycles: null,
  model_version: "v1",
  factors_json: factors,
  computed_at: "2026-10-10T10:00:00Z",
});

const rows = buildHealthRows(
  [
    asset("a", "Feed Pump", "pump"),
    asset("b", "Chiller CH-1", "chiller"),
    asset("c", "Quiet Fan", "fan"),
    asset("d", "Unscored Lathe", "lathe"),
    asset("e", "Humming Press", "press"),
  ],
  [
    score("a", 90),
    score("b", 50),
    score("c", 5),
    score("e", 5, { vibration_alarm: 1 }),
  ]
);
const names = (r: typeof rows) => r.map((x) => x.asset.name).sort();

describe("applyQuickFilter", () => {
  it("'all' keeps everything", () => {
    expect(applyQuickFilter(rows, "all")).toHaveLength(5);
  });

  it("'attention' keeps failing and warning assets (including a quiet vibration alarm), not healthy or unscored", () => {
    expect(names(applyQuickFilter(rows, "attention"))).toEqual(["Chiller CH-1", "Feed Pump", "Humming Press"]);
  });

  it("'alarms' keeps only vibration alarms", () => {
    expect(names(applyQuickFilter(rows, "alarms"))).toEqual(["Humming Press"]);
  });

  it("'unscored' keeps only assets with no risk score", () => {
    expect(names(applyQuickFilter(rows, "unscored"))).toEqual(["Unscored Lathe"]);
  });
});

describe("searchRows", () => {
  it("matches name or type, ignoring case and surrounding spaces", () => {
    expect(names(searchRows(rows, "  CHILLER "))).toEqual(["Chiller CH-1"]);
    expect(names(searchRows(rows, "fan"))).toEqual(["Quiet Fan"]);
    expect(names(searchRows(rows, "lathe"))).toEqual(["Unscored Lathe"]);
  });

  it("treats a blank query as no filter and a miss as empty", () => {
    expect(searchRows(rows, "   ")).toHaveLength(5);
    expect(searchRows(rows, "zzz")).toEqual([]);
  });
});

describe("URL parsing", () => {
  it("falls back to 'all' for unknown or missing quick filters", () => {
    expect(parseQuickFilter("alarms")).toBe("alarms");
    expect(parseQuickFilter("bogus")).toBe("all");
    expect(parseQuickFilter(null)).toBe("all");
  });

  it("accepts only a floor the facility really has", () => {
    expect(parseFloorParam("1", [0, 1, 2])).toBe(1);
    expect(parseFloorParam("0", [0, 1])).toBe(0);
    expect(parseFloorParam("-1", [-1, 0])).toBe(-1);
    for (const bad of [null, "", "  ", "abc", "9", "1.5", "Infinity"]) {
      expect(parseFloorParam(bad, [0, 1, 2])).toBeNull();
    }
  });
});
