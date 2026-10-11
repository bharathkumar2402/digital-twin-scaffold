import { describe, expect, it } from "vitest";

import { buildHealthRows } from "./assetHealth";
import { assetStatus, RUL_URGENT_CYCLES } from "./assetStatus";
import type { Asset, RiskScore } from "../types/api";

const asset: Asset = {
  id: "a",
  facility_id: "f1",
  name: "A",
  type: "pump",
  floor_level: 0,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
};

function status(score: Partial<RiskScore> | null) {
  const scores: RiskScore[] =
    score === null
      ? []
      : [
          {
            id: "r",
            facility_id: "f1",
            asset_id: "a",
            score: 10,
            rul_cycles: null,
            model_version: "v1",
            factors_json: {},
            computed_at: "2026-10-10T10:00:00Z",
            ...score,
          },
        ];
  return assetStatus(buildHealthRows([asset], scores)[0]);
}

describe("assetStatus", () => {
  it.each([
    [66.9, "warning", "Watch closely"],
    [67, "critical", "Failing soon"],
    [34, "warning", "Watch closely"],
    [33.9, "healthy", "Healthy"],
    [0, "healthy", "Healthy"],
  ])("risk %s -> %s (%s)", (risk, key, label) => {
    expect(status({ score: risk })).toMatchObject({ key, label });
  });

  it("treats remaining life at the urgent limit as failing soon even when risk is low", () => {
    expect(status({ score: 5, rul_cycles: RUL_URGENT_CYCLES })).toMatchObject({ key: "critical" });
    expect(status({ score: 5, rul_cycles: RUL_URGENT_CYCLES + 0.1 })).toMatchObject({ key: "healthy" });
  });

  it("treats a real zero-cycle life as failing soon, not as missing", () => {
    expect(status({ score: 5, rul_cycles: 0 })).toMatchObject({ key: "critical" });
  });

  it("flags a vibration alarm on a low-risk asset as a warning, not healthy", () => {
    const s = status({
      score: 0.8,
      factors_json: { vibration_alarm: 1, vibration_alarm_channels: 2, vibration_channels_scored: 2 },
    });
    expect(s).toMatchObject({ key: "warning", label: "Vibration warning" });
    expect(s.reasons.join(" ")).toContain("vibration alarm on 2 of 2 sensors");
  });

  it("lets high risk win over a vibration warning", () => {
    expect(status({ score: 90, factors_json: { vibration_alarm: 1 } })).toMatchObject({ key: "critical" });
  });

  it("says 'Not enough data' for an unscored asset, never healthy", () => {
    const s = status(null);
    expect(s).toMatchObject({ key: "unknown", label: "Not enough data" });
    expect(s.reasons).toEqual(["the risk model has not scored this asset"]);
  });

  it("still warns for an unscored asset that has a vibration alarm", () => {
    expect(status({ score: 0, factors_json: { vibration_alarm: 1 } }).key).toBe("warning");
  });

  it("ranks critical before warning before unknown before healthy", () => {
    const ranks = [status({ score: 90 }), status({ score: 50 }), status(null), status({ score: 1 })].map(
      (s) => s.rank
    );
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    expect(new Set(ranks).size).toBe(4);
  });

  it("gives every status a suggested action", () => {
    for (const s of [status({ score: 90 }), status({ score: 50 }), status(null), status({ score: 1 })]) {
      expect(s.action.length).toBeGreaterThan(0);
    }
  });
});
