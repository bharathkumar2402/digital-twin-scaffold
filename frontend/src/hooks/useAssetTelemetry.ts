import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { TelemetryReading } from "../types/api";

// How often an open telemetry chart asks for newer readings. The hook only exists while the asset panel
// is open, and React Query does not poll a hidden tab, so closing the panel or leaving the tab stops it.
export const TELEMETRY_REFETCH_MS = 15_000;

export function useAssetTelemetry(facilityId: string, assetId: string) {
  return useQuery({
    queryKey: ["asset-telemetry", facilityId, assetId] as const,
    queryFn: () =>
      apiFetch<TelemetryReading[]>(`/facilities/${facilityId}/assets/${assetId}/telemetry`),
    enabled: Boolean(facilityId) && Boolean(assetId),
    refetchInterval: TELEMETRY_REFETCH_MS,
  });
}
