import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { RiskScore } from "../types/api";
import { useFallbackInterval } from "./useLiveState";

export function useRiskScores(facilityId: string) {
  return useQuery({
    queryKey: ["risk-scores", facilityId] as const,
    queryFn: () => apiFetch<RiskScore[]>(`/facilities/${facilityId}/risk-scores`),
    enabled: Boolean(facilityId),
    // A push refreshes this; the poll is only the fallback while the socket is down.
    refetchInterval: useFallbackInterval(),
  });
}
