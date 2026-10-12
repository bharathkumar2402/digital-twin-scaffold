import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { TelemetryFreshness } from "../types/api";
import { FRESHNESS_REFETCH_MS, useTelemetryFreshness } from "./useTelemetryFreshness";

vi.mock("../lib/apiClient", () => ({ apiFetch: vi.fn() }));

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useTelemetryFreshness", () => {
  beforeEach(() => vi.mocked(apiFetch).mockReset());

  it("asks the facility's freshness route", async () => {
    const body: TelemetryFreshness = { as_of: "2026-10-12T12:00:00Z", assets: [] };
    vi.mocked(apiFetch).mockResolvedValue(body);

    const { result } = renderHook(() => useTelemetryFreshness("f1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/telemetry-freshness");
    expect(result.current.data).toEqual(body);
  });

  it("does not fetch without a facility, and refreshes well inside the 5 minute window", () => {
    renderHook(() => useTelemetryFreshness(""), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
    expect(FRESHNESS_REFETCH_MS).toBeLessThanOrEqual(60_000);
  });
});
