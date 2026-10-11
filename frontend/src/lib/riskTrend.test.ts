import { describe, expect, it } from "vitest";

import type { RiskHistoryPoint } from "../types/api";
import {
  describeTrend,
  FLAT_BAND_POINTS,
  formatElapsed,
  BASELINE_TARGET_HOURS,
  MIN_BASELINE_GAP_HOURS,
  trendOf,
} from "./riskTrend";

const pt = (hour: number, score: number): RiskHistoryPoint => ({
  computed_at: new Date(Date.UTC(2026, 9, 1, 0) + hour * 3_600_000).toISOString(),
  score,
  rul_cycles: null,
});

describe("trendOf", () => {
  it("is null with no scores or just one: there is nothing to compare", () => {
    expect(trendOf([])).toBeNull();
    expect(trendOf([pt(8, 50)])).toBeNull();
  });

  it("compares the latest score with the one closest to a day earlier", () => {
    // 95.8 now; the re-scores of the same data hours before are ignored; yesterday's 73.3 wins
    const series = [pt(0, 5), pt(10, 73.3), pt(30, 95.8), pt(33, 95.8), pt(34, 95.8)];
    expect(trendOf(series)).toMatchObject({ direction: "up", elapsedHours: 24 });
    expect(trendOf(series)?.delta).toBeCloseTo(22.5, 5);
  });

  it("does not let a burst of identical re-scores hide a real climb", () => {
    const series = [pt(0, 20), pt(24, 60), pt(24.1, 60), pt(24.2, 60), pt(24.3, 60)];
    expect(trendOf(series)?.direction).toBe("up");
  });

  it("matches the demo's shape: daily history, then a cluster of identical re-scores today", () => {
    // 6 daily scores climbing, then re-scores at 95.8 spread over the last 6.5 hours
    const daily = [0, 24, 48, 72, 96, 120].map((h, i) => pt(h, [8, 15, 26, 39, 55, 73.3][i]));
    const rescores = [138, 140, 142, 143.5, 144.5, 150].map((h) => pt(h, 95.8));
    const trend = trendOf([...daily, ...rescores]);
    expect(trend?.direction).toBe("up");
    expect(trend?.delta).toBeCloseTo(22.5, 5);
    expect(trend?.elapsedHours).toBe(30);
  });

  it("never uses a score younger than the minimum gap as the baseline", () => {
    // a score 11.9 h old is still too recent; the only older one is the baseline
    expect(trendOf([pt(0, 10), pt(100, 20), pt(111.9, 90)])).toMatchObject({ delta: 80 });
    // exactly at the gap counts as old enough
    expect(trendOf([pt(0, 10), pt(MIN_BASELINE_GAP_HOURS, 50)])).toMatchObject({ delta: 40 });
    expect(BASELINE_TARGET_HOURS).toBeGreaterThan(MIN_BASELINE_GAP_HOURS);
  });

  it("with only short-lived history, compares with the oldest score and says how long", () => {
    expect(trendOf([pt(0, 10), pt(5.9, 50)])).toMatchObject({ delta: 40, elapsedHours: 5.9 });
  });

  it("falls back to the oldest score when every score is recent", () => {
    expect(trendOf([pt(8, 10), pt(9, 30), pt(10, 70)])).toMatchObject({ delta: 60, elapsedHours: 2 });
  });

  it("reports up and down", () => {
    expect(trendOf([pt(8, 10), pt(11, 22.4)])).toMatchObject({ direction: "up", elapsedHours: 3 });
    expect(trendOf([pt(8, 40), pt(9, 20)])?.direction).toBe("down");
  });

  it("treats a move smaller than the flat band as steady, and exactly the band as a change", () => {
    expect(trendOf([pt(8, 50), pt(9, 50 + FLAT_BAND_POINTS - 0.1)])?.direction).toBe("flat");
    expect(trendOf([pt(8, 50), pt(9, 50 - FLAT_BAND_POINTS + 0.1)])?.direction).toBe("flat");
    expect(trendOf([pt(8, 50), pt(9, 50 + FLAT_BAND_POINTS)])?.direction).toBe("up");
    expect(trendOf([pt(8, 50), pt(9, 50 - FLAT_BAND_POINTS)])?.direction).toBe("down");
  });

  it("copes with points that arrive out of order, and with equal timestamps", () => {
    expect(trendOf([pt(9, 80), pt(8, 10)])).toMatchObject({ direction: "up", delta: 70 });
    expect(trendOf([pt(8, 10), pt(8, 40)])?.elapsedHours).toBe(0);
  });
});

describe("formatElapsed", () => {
  it.each([
    [0, "1 min"],
    [0.5, "30 min"],
    [1, "1 h"],
    [3.4, "3 h"],
    [47.9, "48 h"],
    [48, "2 days"],
    [100, "4 days"],
  ])("%s hours -> %s", (hours, text) => {
    expect(formatElapsed(hours)).toBe(text);
  });
});

describe("describeTrend", () => {
  it("says how far and how fast, with a sign-free magnitude", () => {
    expect(describeTrend({ direction: "up", delta: 12.44, elapsedHours: 3 })).toBe("up 12.4 pts in 3 h");
    expect(describeTrend({ direction: "down", delta: -3, elapsedHours: 72 })).toBe("down 3.0 pts in 3 days");
  });

  it("says steady for a flat trend", () => {
    expect(describeTrend({ direction: "flat", delta: 0.4, elapsedHours: 30 })).toBe(
      "steady over the last 30 h"
    );
  });
});
