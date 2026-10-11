import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { RiskHistorySeries } from "../types/api";
import { useRiskHistory } from "./useRiskHistory";

vi.mock("../lib/apiClient", () => ({ apiFetch: vi.fn() }));

const series: RiskHistorySeries[] = [
  {
    asset_id: "a1",
    points: [
      { computed_at: "2026-10-01T08:00:00Z", score: 10, rul_cycles: null },
      { computed_at: "2026-10-01T09:00:00Z", score: 30, rul_cycles: 50 },
    ],
  },
  { asset_id: "a2", points: [{ computed_at: "2026-10-01T09:00:00Z", score: 5, rul_cycles: null }] },
];

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useRiskHistory", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("asks for the bounded per-asset window and keys the series by asset id", async () => {
    vi.mocked(apiFetch).mockResolvedValue(series);
    const { result } = renderHook(() => useRiskHistory("f1", 7), { wrapper });
    await waitFor(() => expect(result.current.byAsset.size).toBe(2));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/risk-history?limit_per_asset=7");
    expect(result.current.byAsset.get("a1")).toHaveLength(2);
    expect(result.current.byAsset.get("a2")?.[0].score).toBe(5);
  });

  it("defaults to twenty scores per asset", async () => {
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderHook(() => useRiskHistory("f1"), { wrapper });
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/risk-history?limit_per_asset=20");
  });

  it("degrades to an empty map when the request fails, so screens still render", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("500"));
    const { result } = renderHook(() => useRiskHistory("f1"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.byAsset.size).toBe(0);
  });

  it("does not fetch without a facility id", () => {
    renderHook(() => useRiskHistory(""), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
