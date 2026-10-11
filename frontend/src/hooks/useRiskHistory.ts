import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiFetch } from "../lib/apiClient";
import type { RiskHistoryPoint, RiskHistorySeries } from "../types/api";

// Each asset's last few scores, oldest first, keyed by asset id. Trends are an extra: if this fails
// or is still loading, `byAsset` is just empty and the screens show everything else as normal.
export function useRiskHistory(facilityId: string, limitPerAsset = 20) {
  const query = useQuery({
    queryKey: ["risk-history", facilityId, limitPerAsset] as const,
    queryFn: () =>
      apiFetch<RiskHistorySeries[]>(
        `/facilities/${facilityId}/risk-history?limit_per_asset=${limitPerAsset}`
      ),
    enabled: Boolean(facilityId),
  });
  const byAsset = useMemo(
    () => new Map<string, RiskHistoryPoint[]>((query.data ?? []).map((s) => [s.asset_id, s.points])),
    [query.data]
  );
  return { byAsset, isError: query.isError };
}
