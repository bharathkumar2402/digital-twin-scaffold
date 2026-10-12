import { describe, expect, it } from "vitest";

import { formatUpdatedAgo, serverNowMs } from "./updatedAgo";

describe("formatUpdatedAgo", () => {
  it.each([
    [0, "just now"],
    [59_999, "just now"],
    [60_000, "1 min ago"],
    [3 * 60_000 + 30_000, "3 min ago"],
    [59 * 60_000 + 59_000, "59 min ago"],
    [60 * 60_000, "1 h ago"],
    [47 * 3_600_000, "47 h ago"],
    [48 * 3_600_000, "2 days ago"],
    [-5000, "just now"], // a score slightly ahead of the comparison clock is never negative
  ])("%d ms -> %s", (ms, text) => {
    expect(formatUpdatedAgo(ms)).toBe(text);
  });
});

describe("serverNowMs", () => {
  it("is the server time at the last answer plus the time since it arrived, whatever the browser clock says", () => {
    const clock = { asOfMs: Date.parse("2026-10-12T12:00:00Z"), fetchedAtMs: 1_000_000 };
    // the browser clock is years off, but only the elapsed time since the answer is used
    expect(serverNowMs(clock, 1_000_000)).toBe(clock.asOfMs);
    expect(serverNowMs(clock, 1_000_000 + 90_000)).toBe(clock.asOfMs + 90_000);
  });

  it("never runs backwards if the browser clock steps back", () => {
    const clock = { asOfMs: 5000, fetchedAtMs: 10_000 };
    expect(serverNowMs(clock, 9000)).toBe(5000);
  });

  it("falls back to the browser clock when there is no server clock or it is garbage", () => {
    expect(serverNowMs(null, 123)).toBe(123);
    expect(serverNowMs({ asOfMs: NaN, fetchedAtMs: 1 }, 123)).toBe(123);
    expect(serverNowMs({ asOfMs: 1, fetchedAtMs: Infinity }, 123)).toBe(123);
  });
});
