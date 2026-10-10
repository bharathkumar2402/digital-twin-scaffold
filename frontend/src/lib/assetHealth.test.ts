import { describe, expect, it } from "vitest";

import type { Asset, RiskScore } from "../types/api";
import { buildHealthRows, formatRul, riskBand } from "./assetHealth";

function asset(id: string, name: string): Asset {
  return {
    id,
    facility_id: "f1",
    name,
    type: "pump",
    x: 0,
    y: 0,
    status: "operational",
    installed_date: null,
    manufacturer: null,
    model: null,
  };
}

function score(assetId: string, value: number, extra: Partial<RiskScore> = {}): RiskScore {
  return {
    id: `r-${assetId}`,
    facility_id: "f1",
    asset_id: assetId,
    score: value,
    rul_cycles: null,
    model_version: "v1",
    factors_json: {},
    computed_at: "2026-10-10T10:00:00Z",
    ...extra,
  };
}

describe("riskBand", () => {
  it("uses the same band edges as the map markers", () => {
    expect(riskBand(0)).toBe("low");
    expect(riskBand(33.9)).toBe("low");
    expect(riskBand(34)).toBe("medium");
    expect(riskBand(66.9)).toBe("medium");
    expect(riskBand(67)).toBe("high");
    expect(riskBand(100)).toBe("high");
  });
});

describe("buildHealthRows", () => {
  it("never conflates 'no data' with zero", () => {
    const rows = buildHealthRows([asset("a", "A")], [score("a", 10, { rul_cycles: null })]);
    expect(rows[0].rulCycles).toBeNull();
    expect(formatRul(rows[0].rulCycles)).toBe("—");
    expect(formatRul(0)).toBe("0.0"); // zero cycles left is real data, not missing data
  });

  it("marks assets with no risk row as unscored, not low risk", () => {
    const rows = buildHealthRows([asset("a", "A")], []);
    expect(rows[0].risk).toBeNull();
    expect(rows[0].vibration).toBeNull();
    expect(rows[0].scoredAt).toBeNull();
  });

  it("sorts most worrying first and unscored last", () => {
    const rows = buildHealthRows(
      [asset("a", "Alpha"), asset("b", "Bravo"), asset("c", "Charlie"), asset("d", "Delta")],
      [score("a", 5), score("c", 90), score("b", 90, { rul_cycles: 3.6 })]
    );
    expect(rows.map((r) => r.asset.name)).toEqual(["Bravo", "Charlie", "Alpha", "Delta"]);
  });

  it("unpacks the RUL and vibration verdict from factors_json with their provenance", () => {
    const rows = buildHealthRows(
      [asset("a", "A")],
      [
        score("a", 95.8, {
          rul_cycles: 3.6,
          factors_json: {
            rul_model_version: "rul-1",
            rul_dataset_name: "NASA C-MAPSS",
            vibration_alarm: 1,
            vibration_alarm_channels: 2,
            vibration_channels_scored: 2,
            vibration_max_score: 0.73,
            vibration_threshold: 0.55,
            vibration_model_version: "vib-1",
            vibration_dataset_name: "NASA IMS",
          },
        }),
      ]
    );
    expect(rows[0]).toMatchObject({
      rulCycles: 3.6,
      rulModelVersion: "rul-1",
      rulDatasetName: "NASA C-MAPSS",
      vibration: {
        alarm: true,
        alarmChannels: 2,
        maxScore: 0.73,
        threshold: 0.55,
        modelVersion: "vib-1",
        datasetName: "NASA IMS",
      },
    });
  });

  it("distinguishes 'vibration checked, no alarm' from 'no vibration data'", () => {
    const checked = buildHealthRows(
      [asset("a", "A")],
      [score("a", 1, { factors_json: { vibration_alarm: 0 } })]
    )[0];
    const none = buildHealthRows([asset("a", "A")], [score("a", 1)])[0];
    expect(checked.vibration?.alarm).toBe(false);
    expect(none.vibration).toBeNull();
  });

  it("records why vibration was unavailable", () => {
    const row = buildHealthRows(
      [asset("a", "A")],
      [score("a", 1, { factors_json: { vibration_unavailable_reason: "invalid_waveform" } })]
    )[0];
    expect(row.vibration).toBeNull();
    expect(row.vibrationUnavailableReason).toBe("invalid_waveform");
  });

  it("keeps the newest score if the API ever returns an asset twice", () => {
    const rows = buildHealthRows(
      [asset("a", "A")],
      [
        score("a", 10, { computed_at: "2026-10-10T09:00:00Z" }),
        score("a", 80, { computed_at: "2026-10-10T11:00:00Z" }),
      ]
    );
    expect(rows[0].risk).toBe(80);
  });

  it("ignores scores for assets that are not in the list", () => {
    expect(buildHealthRows([], [score("ghost", 99)])).toEqual([]);
  });
});
