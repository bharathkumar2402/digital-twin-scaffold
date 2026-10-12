import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveContext, NO_LIVE } from "../hooks/useLiveState";
import { UpdatedAgo } from "./UpdatedAgo";

const NOW = Date.parse("2026-10-12T12:00:00Z");

function show(scoredAt: string | null, connected: boolean, clock = null as null | { asOfMs: number; fetchedAtMs: number }) {
  return render(
    <LiveContext.Provider value={{ ...NO_LIVE, connected }}>
      <UpdatedAgo scoredAt={scoredAt} clock={clock} />
    </LiveContext.Provider>
  );
}

describe("UpdatedAgo", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    [10_000, "Updated just now"],
    [90_000, "Updated 1 min ago"],
    [7 * 60_000, "Updated 7 min ago"],
    [3 * 3_600_000, "Updated 3 h ago"],
  ])("%d ms old -> %s", (age, text) => {
    show(new Date(NOW - age).toISOString(), true);
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it("says Live while the push connection is open, and that it is polling when it is not", () => {
    const { unmount } = show(new Date(NOW - 5000).toISOString(), true);
    expect(screen.getByRole("status")).toHaveTextContent("Live");
    expect(screen.getByRole("status")).not.toHaveTextContent("Reconnecting");
    unmount();

    show(new Date(NOW - 5000).toISOString(), false);
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting, refreshing every minute");
    expect(screen.getByRole("status")).not.toHaveTextContent("Live");
  });

  it("does not claim an update when nothing was ever scored, or the time is garbage", () => {
    const { unmount } = show(null, true);
    expect(screen.getByRole("status")).toHaveTextContent("Not scored yet");
    unmount();
    show("not a date", true);
    expect(screen.getByRole("status")).toHaveTextContent("Not scored yet");
  });

  it("keeps counting up by itself", () => {
    show(new Date(NOW - 30_000).toISOString(), true);
    expect(screen.getByRole("status")).toHaveTextContent("just now");
    act(() => {
      vi.advanceTimersByTime(5 * 60_000);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Updated 5 min ago");
  });

  it("measures age on the server's clock when it is known, so a wrong browser clock cannot fake it", () => {
    // the browser says it is 12:00, the server said (a moment ago) that it was 12:30
    const clock = { asOfMs: NOW + 30 * 60_000, fetchedAtMs: NOW };
    show(new Date(NOW + 30 * 60_000 - 4 * 60_000).toISOString(), true, clock);
    expect(screen.getByRole("status")).toHaveTextContent("Updated 4 min ago");
  });
});
