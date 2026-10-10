// View-model for the Results page's asset health table: joins assets with their latest risk
// score and unpacks the RUL / vibration verdict that scoring stores in `factors_json`.
import type { Asset, RiskScore } from "../types/api";

export type RiskBand = "low" | "medium" | "high";

// Same bands (and colors) as the map's markers - see AssetLayer.tsx - so a dot on the map and
// a row in this table never disagree: 0-33 low, 34-66 medium, 67-100 high.
export function riskBand(score: number): RiskBand {
  if (score >= 67) return "high";
  if (score >= 34) return "medium";
  return "low";
}

export const RISK_BAND_COLOR: Record<RiskBand, string> = {
  low: "#2e7d32",
  medium: "#ed6c02",
  high: "#c62828",
};

export interface VibrationVerdict {
  alarm: boolean;
  alarmChannels: number | null;
  channelsScored: number | null;
  maxScore: number | null;
  threshold: number | null;
  modelVersion: string | null;
  datasetName: string | null;
}

export interface AssetHealthRow {
  asset: Asset;
  // null = never scored (no usable AI4I snapshot, or scoring has not run) - not "0".
  risk: number | null;
  // null = no RUL available - not "0", which would mean "fails now".
  rulCycles: number | null;
  rulModelVersion: string | null;
  rulDatasetName: string | null;
  // null = no vibration verdict for this asset.
  vibration: VibrationVerdict | null;
  vibrationUnavailableReason: string | null;
  riskModelVersion: string | null;
  scoredAt: string | null;
}

function num(value: number | string | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: number | string | null | undefined): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function vibrationFrom(factors: RiskScore["factors_json"]): VibrationVerdict | null {
  if (!("vibration_alarm" in factors)) return null;
  return {
    alarm: num(factors.vibration_alarm) === 1,
    alarmChannels: num(factors.vibration_alarm_channels),
    channelsScored: num(factors.vibration_channels_scored),
    maxScore: num(factors.vibration_max_score),
    threshold: num(factors.vibration_threshold),
    modelVersion: str(factors.vibration_model_version),
    datasetName: str(factors.vibration_dataset_name),
  };
}

export function buildHealthRows(assets: Asset[], scores: RiskScore[]): AssetHealthRow[] {
  // The API returns the latest row per asset, but be defensive: keep the newest if repeated.
  const latest = new Map<string, RiskScore>();
  for (const score of scores) {
    const existing = latest.get(score.asset_id);
    if (!existing || score.computed_at > existing.computed_at) latest.set(score.asset_id, score);
  }

  const rows = assets.map((asset): AssetHealthRow => {
    const score = latest.get(asset.id) ?? null;
    const factors = score?.factors_json ?? {};
    return {
      asset,
      risk: score ? score.score : null,
      rulCycles: score ? score.rul_cycles : null,
      rulModelVersion: str(factors.rul_model_version),
      rulDatasetName: str(factors.rul_dataset_name),
      vibration: vibrationFrom(factors),
      vibrationUnavailableReason: str(factors.vibration_unavailable_reason),
      riskModelVersion: score ? score.model_version : null,
      scoredAt: score ? score.computed_at : null,
    };
  });

  // Most worrying first: scored assets by descending risk (ties: lower RUL first), then
  // assets that were never scored, by name.
  return rows.sort((a, b) => {
    if (a.risk === null && b.risk === null) return a.asset.name.localeCompare(b.asset.name);
    if (a.risk === null) return 1;
    if (b.risk === null) return -1;
    if (b.risk !== a.risk) return b.risk - a.risk;
    return (a.rulCycles ?? Infinity) - (b.rulCycles ?? Infinity);
  });
}

export function formatRul(cycles: number | null): string {
  return cycles === null ? "—" : cycles.toFixed(1);
}
