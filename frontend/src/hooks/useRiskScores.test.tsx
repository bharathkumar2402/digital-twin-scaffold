import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { RiskScore } from "../types/api";
import { useRiskScores } from "./useRiskScores";

vi.mock("../lib/apiClient", () => ({
  apiFetch: vi.fn(),
}));

const riskScore: RiskScore = {
  id: "r1",
  facility_id: "f1",
  asset_id: "a1",
  score: 72.5,
  model_version: "1",
  factors_json: {},
  computed_at: "2026-01-01T00:00:00Z",
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useRiskScores", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("fetches the latest risk scores for the given facility", async () => {
    vi.mocked(apiFetch).mockResolvedValue([riskScore]);

    const { result } = renderHook(() => useRiskScores("f1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/risk-scores");
    expect(result.current.data).toEqual([riskScore]);
  });

  it("does not fetch when facilityId is empty", () => {
    renderHook(() => useRiskScores(""), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
