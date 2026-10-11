import { describe, expect, it } from "vitest";

import type { RiskHistoryPoint } from "../types/api";
import {
  BAND_EDGES,
  CHART,
  enteredHighRisk,
  formatAxisTime,
  isFlatSeries,
  layoutPoints,
  rulRange,
  yFor,
} from "./riskChart";

const at = (hours: number, score: number, rul: number | null = null): RiskHistoryPoint => ({
  computed_at: new Date(Date.UTC(2026, 9, 1, 0) + hours * 3_600_000).toISOString(),
  score,
  rul_cycles: rul,
});

describe("yFor", () => {
  it("puts 100 at the top of the plot and 0 at the bottom, higher risk higher up", () => {
    expect(yFor(100)).toBe(CHART.top);
    expect(yFor(0)).toBe(CHART.height - CHART.bottom);
    expect(yFor(80)).toBeLessThan(yFor(20));
  });

  it("keeps out-of-range scores inside the plot", () => {
    expect(yFor(250)).toBe(yFor(100));
    expect(yFor(-40)).toBe(yFor(0));
  });
});

describe("layoutPoints", () => {
  it("spaces points by TIME, not by count", () => {
    const laid = layoutPoints([at(0, 10), at(1, 20), at(10, 30)]);
    const gapShort = laid[1].x - laid[0].x;
    const gapLong = laid[2].x - laid[1].x;
    expect(gapLong).toBeCloseTo(gapShort * 9, 5);
  });

  it("spans the whole plot from first to last point", () => {
    const laid = layoutPoints([at(0, 10), at(5, 20)]);
    expect(laid[0].x).toBe(CHART.left);
    expect(laid[1].x).toBe(CHART.width - CHART.right);
  });

  it("sorts out-of-order input oldest first", () => {
    const laid = layoutPoints([at(5, 90), at(0, 10)]);
    expect(laid.map((p) => p.point.score)).toEqual([10, 90]);
    expect(laid[0].x).toBeLessThan(laid[1].x);
  });

  it("falls back to even spacing when every score shares one timestamp", () => {
    const laid = layoutPoints([at(0, 1), at(0, 2), at(0, 3)]);
    expect(laid.map((p) => p.x)).toEqual([
      CHART.left,
      CHART.left + (CHART.width - CHART.left - CHART.right) / 2,
      CHART.width - CHART.right,
    ]);
  });

  it("centres a single point and returns nothing for no points", () => {
    expect(layoutPoints([])).toEqual([]);
    const [only] = layoutPoints([at(0, 50)]);
    expect(only.x).toBeCloseTo((CHART.left + CHART.width - CHART.right) / 2, 5);
  });
});

describe("enteredHighRisk", () => {
  it("finds the first score of the current run of high risk", () => {
    const series = [at(0, 10), at(1, 40), at(2, 70), at(3, 85), at(4, 95)];
    expect(enteredHighRisk(series)).toEqual({ index: 2, at: series[2].computed_at });
  });

  it("uses the LATEST crossing when it dipped out of high and came back", () => {
    const series = [at(0, 80), at(1, 20), at(2, 75), at(3, 90)];
    expect(enteredHighRisk(series)?.index).toBe(2);
  });

  it("treats exactly the high threshold as high", () => {
    const series = [at(0, BAND_EDGES.high - 0.1), at(1, BAND_EDGES.high)];
    expect(enteredHighRisk(series)?.index).toBe(1);
  });

  it("is null when the asset is not high now, was high the whole window, or has no points", () => {
    expect(enteredHighRisk([at(0, 90), at(1, 30)])).toBeNull();
    expect(enteredHighRisk([at(0, 90), at(1, 95)])).toBeNull();
    expect(enteredHighRisk([at(0, 10), at(1, 20)])).toBeNull();
    expect(enteredHighRisk([])).toBeNull();
  });
});

describe("isFlatSeries", () => {
  it("is flat only when the whole window moved less than the flat band", () => {
    expect(isFlatSeries([at(0, 95.8), at(1, 95.8), at(2, 95.8)])).toBe(true);
    expect(isFlatSeries([at(0, 50), at(1, 51.5), at(2, 50.5)])).toBe(true);
    expect(isFlatSeries([at(0, 50), at(1, 60), at(2, 50)])).toBe(false);
  });

  it("is not 'flat' with fewer than two scores (there is nothing to compare)", () => {
    expect(isFlatSeries([])).toBe(false);
    expect(isFlatSeries([at(0, 50)])).toBe(false);
  });
});

describe("rulRange", () => {
  it("gives the first and last remaining-life values", () => {
    expect(rulRange([at(0, 10, 120), at(1, 20, null), at(2, 30, 3.6)])).toEqual({ from: 120, to: 3.6 });
  });

  it("needs at least two remaining-life values", () => {
    expect(rulRange([at(0, 10, 120), at(1, 20, null)])).toBeNull();
    expect(rulRange([at(0, 10), at(1, 20)])).toBeNull();
  });

  it("treats a real zero as a value, not as missing", () => {
    expect(rulRange([at(0, 10, 5), at(1, 20, 0)])).toEqual({ from: 5, to: 0 });
  });
});

describe("formatAxisTime", () => {
  it("shows a clock time for a window under a day and a date for longer", () => {
    const iso = "2026-10-05T15:30:00Z";
    expect(formatAxisTime(iso, 6)).toMatch(/\d{1,2}:\d{2}/);
    expect(formatAxisTime(iso, 72)).not.toMatch(/:/);
    expect(formatAxisTime(iso, 72)).toMatch(/Oct/);
  });
});
