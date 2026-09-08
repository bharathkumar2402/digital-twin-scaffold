import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { TelemetryReading } from "../types/api";

export function useAssetTelemetry(facilityId: string, assetId: string) {
  return useQuery({
    queryKey: ["asset-telemetry", facilityId, assetId] as const,
    queryFn: () =>
      apiFetch<TelemetryReading[]>(`/facilities/${facilityId}/assets/${assetId}/telemetry`),
    enabled: Boolean(facilityId) && Boolean(assetId),
  });
}
