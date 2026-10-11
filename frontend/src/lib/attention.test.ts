import { describe, expect, it } from "vitest";

import { buildHealthRows } from "./assetHealth";
import { latestScoredAt, suggestedQuestions, topAttention } from "./attention";
import type { Asset, RiskScore } from "../types/api";

const asset = (id: string, name: string): Asset => ({
  id,
  facility_id: "f1",
  name,
  type: "pump",
  floor_level: 0,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
});

const score = (
  assetId: string,
  value: number,
  over: Partial<RiskScore> = {}
): RiskScore => ({
  id: `r-${assetId}`,
  facility_id: "f1",
  asset_id: assetId,
  score: value,
  rul_cycles: null,
  model_version: "v1",
  factors_json: {},
  computed_at: "2026-10-10T10:00:00Z",
  ...over,
});

function rowsFor(spec: [string, number, Partial<RiskScore>?][]) {
  return buildHealthRows(
    spec.map(([name]) => asset(name, name)),
    spec.map(([name, value, over]) => score(name, value, over))
  );
}

const names = (rows: ReturnType<typeof rowsFor>) => rows.map((r) => r.asset.name);

describe("topAttention", () => {
  it("puts failing assets before warnings, then orders by risk", () => {
    const { top } = topAttention(
      rowsFor([
        ["warn-high", 60],
        ["fail-low", 70],
        ["fail-high", 95],
        ["warn-low", 40],
      ])
    );
    expect(names(top)).toEqual(["fail-high", "fail-low", "warn-high", "warn-low"]);
  });

  it("breaks a risk tie by shorter remaining life, then by name", () => {
    const { top } = topAttention(
      rowsFor([
        ["b-asset", 80, { rul_cycles: 50 }],
        ["a-asset", 80, { rul_cycles: 50 }],
        ["c-asset", 80, { rul_cycles: 10 }],
        ["d-asset", 80],
      ])
    );
    expect(names(top)).toEqual(["c-asset", "a-asset", "b-asset", "d-asset"]);
  });

  it("never lists healthy or unscored assets, but lists a quiet vibration alarm", () => {
    const rows = buildHealthRows(
      [asset("h", "Healthy"), asset("u", "Unscored"), asset("v", "Humming")],
      [score("h", 5), score("v", 3, { factors_json: { vibration_alarm: 1 } })]
    );
    const { top, total } = topAttention(rows);
    expect(names(top)).toEqual(["Humming"]);
    expect(total).toBe(1);
  });

  it("caps the list at the limit but still reports the full count", () => {
    const spec: [string, number][] = Array.from({ length: 8 }, (_, i) => [`m${i}`, 90 - i]);
    const { top, total } = topAttention(rowsFor(spec), 5);
    expect(top).toHaveLength(5);
    expect(total).toBe(8);
    expect(names(top)[0]).toBe("m0");
  });

  it("returns nothing for an empty list and for a limit of zero", () => {
    expect(topAttention([])).toEqual({ top: [], total: 0 });
    expect(topAttention(rowsFor([["x", 90]]), 0)).toEqual({ top: [], total: 1 });
  });

  it("treats a real zero-cycle life as the most urgent, not as missing", () => {
    const { top } = topAttention(
      rowsFor([
        ["none", 80],
        ["zero", 80, { rul_cycles: 0 }],
      ])
    );
    expect(names(top)).toEqual(["zero", "none"]);
  });
});

describe("latestScoredAt", () => {
  it("returns the newest score time, or null when nothing was scored", () => {
    const rows = buildHealthRows(
      [asset("a", "A"), asset("b", "B"), asset("c", "C")],
      [
        score("a", 1, { computed_at: "2026-10-09T10:00:00Z" }),
        score("b", 1, { computed_at: "2026-10-11T08:00:00Z" }),
      ]
    );
    expect(latestScoredAt(rows)).toBe("2026-10-11T08:00:00Z");
    expect(latestScoredAt(buildHealthRows([asset("c", "C")], []))).toBeNull();
  });
});

describe("suggestedQuestions", () => {
  it("asks about the machines needing attention, worst first, up to the limit", () => {
    const rows = rowsFor([
      ["Fine", 5],
      ["Worst", 95],
      ["Warm", 50],
      ["Hot", 80],
    ]);
    expect(suggestedQuestions(rows, 2)).toEqual(["What if Worst fails?", "What if Hot fails?"]);
  });

  it("falls back to the riskiest assets when nothing needs attention", () => {
    const rows = rowsFor([
      ["Calm", 3],
      ["Quiet", 1],
    ]);
    expect(suggestedQuestions(rows, 1)).toEqual(["What if Calm fails?"]);
  });

  it("returns nothing for a facility with no assets", () => {
    expect(suggestedQuestions([])).toEqual([]);
  });
});
