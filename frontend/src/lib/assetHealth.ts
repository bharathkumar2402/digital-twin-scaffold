// View-model for the Results page's asset health table: joins assets with their latest risk
// score and unpacks the RUL / vibration verdict that scoring stores in `factors_json`.
import type { Asset, RiskScore, TelemetryFreshness } from "../types/api";

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

// The same bands as TEXT colours: the marker orange (#ed6c02) is only 3.1:1 on white, so text uses a darker amber
// (5.8:1). Markers, bars and chart fills keep RISK_BAND_COLOR.
export const RISK_BAND_TEXT_COLOR: Record<RiskBand, string> = {
  low: "#2e7d32",
  medium: "#a8480a",
  high: "#c62828",
};

// No reading for this long = "No recent data". Five simulator ticks (15 s each) would be 75 s; five
// minutes tolerates a slow network or a restarted gateway without crying wolf.
export const STALE_AFTER_MS = 5 * 60 * 1000;

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
  // Newest sensor reading of any kind (null = never reported or not looked up yet).
  lastReadingAt: string | null;
  // How old that reading was by the server's clock when it was looked up. undefined = unknown (the
  // lookup has not answered, so nothing is claimed); null = the asset has never reported.
  readingAgeMs: number | null | undefined;
}

// Reporting before and not any more. An asset that never reported at all has no live feed to lose
// (typically a demo or snapshot-scored asset), so it is not "stale"; it keeps its score-based status.
export function isStale(row: Pick<AssetHealthRow, "readingAgeMs">): boolean {
  return typeof row.readingAgeMs === "number" && row.readingAgeMs >= STALE_AFTER_MS;
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

export function buildHealthRows(
  assets: Asset[],
  scores: RiskScore[],
  freshness?: TelemetryFreshness
): AssetHealthRow[] {
  const asOfMs = freshness ? Date.parse(freshness.as_of) : NaN;
  const lastById = new Map((freshness?.assets ?? []).map((f) => [f.asset_id, f.last_reading_at]));
  // The API returns the latest row per asset, but be defensive: keep the newest if repeated.
  const latest = new Map<string, RiskScore>();
  for (const score of scores) {
    const existing = latest.get(score.asset_id);
    if (!existing || score.computed_at > existing.computed_at) latest.set(score.asset_id, score);
  }

  const rows = assets.map((asset): AssetHealthRow => {
    const score = latest.get(asset.id) ?? null;
    const factors = score?.factors_json ?? {};
    const reported = lastById.get(asset.id);
    const lastMs = reported ? Date.parse(reported) : NaN;
    let readingAgeMs: number | null | undefined;
    if (reported === undefined || Number.isNaN(asOfMs)) readingAgeMs = undefined;
    else if (reported === null || Number.isNaN(lastMs)) readingAgeMs = null;
    else readingAgeMs = Math.max(0, asOfMs - lastMs);
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
      lastReadingAt: reported ?? null,
      readingAgeMs,
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

export interface HealthSummary {
  monitored: number;
  // Assets with a risk score / without one.
  scored: number;
  unscored: number;
  // Risk in the map's "high" band (>= 67).
  highRisk: number;
  vibrationAlarms: number;
  // Assets that were reporting and went quiet (see isStale).
  stale: number;
  // The asset closest to failure among those that have an RUL, or null if none do.
  shortestRul: { assetName: string; cycles: number } | null;
}

export function summarizeHealth(rows: AssetHealthRow[]): HealthSummary {
  let shortest: HealthSummary["shortestRul"] = null;
  for (const row of rows) {
    if (row.rulCycles !== null && (shortest === null || row.rulCycles < shortest.cycles)) {
      shortest = { assetName: row.asset.name, cycles: row.rulCycles };
    }
  }
  return {
    monitored: rows.length,
    scored: rows.filter((r) => r.risk !== null).length,
    unscored: rows.filter((r) => r.risk === null).length,
    highRisk: rows.filter((r) => r.risk !== null && riskBand(r.risk) === "high").length,
    vibrationAlarms: rows.filter((r) => r.vibration?.alarm === true).length,
    stale: rows.filter(isStale).length,
    shortestRul: shortest,
  };
}

export interface FloorOption {
  level: number;
  label: string;
}

// Every floor worth offering in the filter: floors with a finished map (named by the facility)
// plus any floor that has assets but no map yet ("Floor N"). Lowest level first.
export function floorOptions(
  rows: AssetHealthRow[],
  floors: { level: number; name: string }[]
): FloorOption[] {
  const byLevel = new Map<number, string>();
  for (const floor of floors) byLevel.set(floor.level, floor.name || `Floor ${floor.level}`);
  for (const row of rows) {
    if (!byLevel.has(row.asset.floor_level)) {
      byLevel.set(row.asset.floor_level, `Floor ${row.asset.floor_level}`);
    }
  }
  return [...byLevel.entries()]
    .sort(([a], [b]) => a - b)
    .map(([level, label]) => ({ level, label }));
}

// level === null means "all floors".
export function filterByFloor(rows: AssetHealthRow[], level: number | null): AssetHealthRow[] {
  return level === null ? rows : rows.filter((row) => row.asset.floor_level === level);
}

export interface FloorHealth extends FloorOption {
  monitored: number;
  highRisk: number;
  vibrationAlarms: number;
}

export function summarizeByFloor(rows: AssetHealthRow[], options: FloorOption[]): FloorHealth[] {
  return options.map((option) => {
    const summary = summarizeHealth(filterByFloor(rows, option.level));
    return {
      ...option,
      monitored: summary.monitored,
      highRisk: summary.highRisk,
      vibrationAlarms: summary.vibrationAlarms,
    };
  });
}

// DOM id of a row's name button; the page returns focus to it when the detail drawer closes.
export function assetRowButtonId(assetId: string): string {
  return `asset-row-${assetId}`;
}
