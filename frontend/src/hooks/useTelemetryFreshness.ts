import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { TelemetryFreshness } from "../types/api";

// How often "when did each asset last report" is re-asked. The stale threshold is 5 minutes, so a
// quiet sensor shows up within one refresh of crossing it.
export const FRESHNESS_REFETCH_MS = 30_000;

export function useTelemetryFreshness(facilityId: string) {
  return useQuery({
    queryKey: ["telemetry-freshness", facilityId] as const,
    queryFn: () => apiFetch<TelemetryFreshness>(`/facilities/${facilityId}/telemetry-freshness`),
    enabled: Boolean(facilityId),
    refetchInterval: FRESHNESS_REFETCH_MS,
  });
}
