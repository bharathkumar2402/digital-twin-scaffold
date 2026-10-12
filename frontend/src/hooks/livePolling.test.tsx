import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import { TELEMETRY_REFETCH_MS, useAssetTelemetry } from "./useAssetTelemetry";
import { LiveContext, NO_LIVE, FALLBACK_POLL_MS } from "./useLiveState";
import { useRiskHistory } from "./useRiskHistory";
import { useRiskScores } from "./useRiskScores";
import { FRESHNESS_REFETCH_MS, useTelemetryFreshness } from "./useTelemetryFreshness";

vi.mock("../lib/apiClient", () => ({ apiFetch: vi.fn() }));

function wrapperFor(connected: boolean) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return (
      <QueryClientProvider client={client}>
        <LiveContext.Provider value={{ ...NO_LIVE, connected }}>{children}</LiveContext.Provider>
      </QueryClientProvider>
    );
  };
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const callsTo = (fragment: string) =>
  vi.mocked(apiFetch).mock.calls.filter(([path]) => String(path).includes(fragment)).length;

describe("polling follows the live connection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue([]);
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    ["risk scores", "risk-scores", () => useRiskScores("f1")],
    ["risk history", "risk-history", () => useRiskHistory("f1")],
  ])("%s poll once a minute while the socket is down", async (_name, fragment, hook: () => unknown) => {
    renderHook(hook, { wrapper: wrapperFor(false) });
    await advance(0);
    expect(callsTo(fragment)).toBe(1);

    await advance(FALLBACK_POLL_MS - 1000);
    expect(callsTo(fragment)).toBe(1); // not before a minute
    await advance(1000);
    expect(callsTo(fragment)).toBe(2);
    await advance(FALLBACK_POLL_MS);
    expect(callsTo(fragment)).toBe(3);
  });

  it.each([
    ["risk scores", "risk-scores", () => useRiskScores("f1")],
    ["risk history", "risk-history", () => useRiskHistory("f1")],
  ])("%s do not poll while the socket is open: a push refreshes them", async (_name, fragment, hook: () => unknown) => {
    renderHook(hook, { wrapper: wrapperFor(true) });
    await advance(0);
    await advance(FALLBACK_POLL_MS * 5);
    expect(callsTo(fragment)).toBe(1);
  });

  it("freshness keeps polling either way, because silence sends no push", async () => {
    renderHook(() => useTelemetryFreshness("f1"), { wrapper: wrapperFor(true) });
    await advance(0);
    await advance(FRESHNESS_REFETCH_MS * 3);
    expect(callsTo("telemetry-freshness")).toBe(4);
  });

  it("an open asset's telemetry chart asks for newer readings every ~15 s, and stops when it closes", async () => {
    expect(TELEMETRY_REFETCH_MS).toBe(15_000);
    const { unmount } = renderHook(() => useAssetTelemetry("f1", "a1"), {
      wrapper: wrapperFor(true),
    });
    await advance(0);
    expect(callsTo("/telemetry")).toBe(1);
    await advance(TELEMETRY_REFETCH_MS * 2);
    expect(callsTo("/telemetry")).toBe(3);

    unmount(); // the panel closed
    await advance(TELEMETRY_REFETCH_MS * 4);
    expect(callsTo("/telemetry")).toBe(3);
  });

  it("does not fetch or poll anything for an empty facility id", async () => {
    renderHook(() => useRiskScores(""), { wrapper: wrapperFor(false) });
    await advance(FALLBACK_POLL_MS * 2);
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
