import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { RiskScore } from "../types/api";

export function useRiskScores(facilityId: string) {
  return useQuery({
    queryKey: ["risk-scores", facilityId] as const,
    queryFn: () => apiFetch<RiskScore[]>(`/facilities/${facilityId}/risk-scores`),
    enabled: Boolean(facilityId),
  });
}
