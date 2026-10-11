import type { RiskHistoryPoint } from "../types/api";
import { FLAT_BAND_POINTS } from "./riskTrend";

// Layout of the risk-trend chart, in SVG units (the SVG scales to its container's width).
export const CHART = { width: 320, height: 132, left: 30, right: 14, top: 12, bottom: 26 };

// The same bands as the map markers and the table: low < 34 <= medium < 67 <= high.
export const BAND_EDGES = { medium: 34, high: 67 } as const;

const PLOT_W = CHART.width - CHART.left - CHART.right;
const PLOT_H = CHART.height - CHART.top - CHART.bottom;

export function yFor(score: number): number {
  const clamped = Math.min(100, Math.max(0, score));
  return CHART.top + ((100 - clamped) / 100) * PLOT_H;
}

export interface LaidOutPoint {
  x: number;
  y: number;
  point: RiskHistoryPoint;
}

// x is proportional to TIME (a gap of three days looks three times as wide as one day), falling
// back to even spacing when every score shares one timestamp. Points are sorted oldest first.
export function layoutPoints(points: RiskHistoryPoint[]): LaidOutPoint[] {
  const sorted = [...points].sort((a, b) => a.computed_at.localeCompare(b.computed_at));
  if (sorted.length === 0) return [];
  const t0 = new Date(sorted[0].computed_at).getTime();
  const t1 = new Date(sorted[sorted.length - 1].computed_at).getTime();
  const span = t1 - t0;
  return sorted.map((point, i) => {
    const fraction =
      span > 0
        ? (new Date(point.computed_at).getTime() - t0) / span
        : sorted.length > 1
          ? i / (sorted.length - 1)
          : 0.5;
    return { x: CHART.left + fraction * PLOT_W, y: yFor(point.score), point };
  });
}

// The moment the latest run of high-risk scores began: the first high score after a non-high one.
// null if the series never crossed (always high, never high) or the asset is not high now.
export function enteredHighRisk(points: RiskHistoryPoint[]): { index: number; at: string } | null {
  const sorted = [...points].sort((a, b) => a.computed_at.localeCompare(b.computed_at));
  const last = sorted[sorted.length - 1];
  if (!last || last.score < BAND_EDGES.high) return null;
  let start = sorted.length - 1;
  while (start > 0 && sorted[start - 1].score >= BAND_EDGES.high) start -= 1;
  if (start === 0) return null; // high for the whole window: there is no crossing to point at
  return { index: start, at: sorted[start].computed_at };
}

// True when the whole window moved by less than the flat band: nothing worth a line.
export function isFlatSeries(points: RiskHistoryPoint[]): boolean {
  if (points.length < 2) return false;
  const scores = points.map((p) => p.score);
  return Math.max(...scores) - Math.min(...scores) < FLAT_BAND_POINTS;
}

// Axis label: a clock time when the window is under a day, a date otherwise.
export function formatAxisTime(iso: string, spanHours: number): string {
  const date = new Date(iso);
  return spanHours < 24
    ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Remaining-life moves smaller than this (in cycles) are not worth a line of text.
export const RUL_MIN_CHANGE = 0.5;

// The first and last remaining-life values, when at least two scores carry one.
export function rulRange(points: RiskHistoryPoint[]): { from: number; to: number } | null {
  const withRul = [...points]
    .sort((a, b) => a.computed_at.localeCompare(b.computed_at))
    .filter((p) => p.rul_cycles !== null);
  if (withRul.length < 2) return null;
  return {
    from: withRul[0].rul_cycles as number,
    to: withRul[withRul.length - 1].rul_cycles as number,
  };
}
