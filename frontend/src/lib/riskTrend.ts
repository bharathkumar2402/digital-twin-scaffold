import type { RiskHistoryPoint } from "../types/api";

export type TrendDirection = "up" | "down" | "flat";

export interface Trend {
  direction: TrendDirection;
  // Latest score minus the baseline score (points on the 0-100 scale).
  delta: number;
  // Time between the baseline and the latest score.
  elapsedHours: number;
}

// A move smaller than this is noise, not a change worth showing.
export const FLAT_BAND_POINTS = 2;

// "Since yesterday": the latest score is compared with the one closest to this far back...
export const BASELINE_TARGET_HOURS = 24;
// ...but never with one younger than this. Scores a few hours apart are usually re-scores of the
// same data, and "up 0.0 in 4 hours" would hide a real climb over the previous day.
export const MIN_BASELINE_GAP_HOURS = 12;

// How the latest score compares with about a day earlier: the score closest to
// BASELINE_TARGET_HOURS before it, among those at least MIN_BASELINE_GAP_HOURS older. If the
// history is too short for that, the oldest score is the baseline. null when there is nothing to
// compare (fewer than two scores). Points are re-sorted defensively.
export function trendOf(points: RiskHistoryPoint[]): Trend | null {
  if (points.length < 2) return null;
  const sorted = [...points].sort((a, b) => a.computed_at.localeCompare(b.computed_at));
  const latest = sorted[sorted.length - 1];
  const latestMs = new Date(latest.computed_at).getTime();
  const target = latestMs - BASELINE_TARGET_HOURS * 3_600_000;
  const minAge = MIN_BASELINE_GAP_HOURS * 3_600_000;
  let baseline = sorted[0];
  let bestDistance = Infinity;
  for (const candidate of sorted.slice(0, -1)) {
    const ms = new Date(candidate.computed_at).getTime();
    if (latestMs - ms < minAge) continue;
    const distance = Math.abs(ms - target);
    if (distance < bestDistance) {
      baseline = candidate;
      bestDistance = distance;
    }
  }
  const delta = latest.score - baseline.score;
  const elapsedHours = Math.max(
    0,
    (latestMs - new Date(baseline.computed_at).getTime()) / 3_600_000
  );
  const direction: TrendDirection =
    Math.abs(delta) < FLAT_BAND_POINTS ? "flat" : delta > 0 ? "up" : "down";
  return { direction, delta, elapsedHours };
}

export function formatElapsed(hours: number): string {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} days`;
}

// "up 12.4 pts in 3 h", "down 3.0 pts in 2 days", or "steady over the last 3 h".
export function describeTrend(trend: Trend): string {
  if (trend.direction === "flat") return `steady over the last ${formatElapsed(trend.elapsedHours)}`;
  const verb = trend.direction === "up" ? "up" : "down";
  return `${verb} ${Math.abs(trend.delta).toFixed(1)} pts in ${formatElapsed(trend.elapsedHours)}`;
}

export const TREND_ARROW: Record<TrendDirection, string> = { up: "▲", down: "▼", flat: "▬" };
