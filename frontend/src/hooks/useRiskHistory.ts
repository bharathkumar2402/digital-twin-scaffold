import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiFetch } from "../lib/apiClient";
import type { RiskHistoryPoint, RiskHistorySeries } from "../types/api";
import { useFallbackInterval } from "./useLiveState";

// Each asset's last few scores, oldest first, keyed by asset id. Trends are an extra: if this fails
// or is still loading, `byAsset` is just empty and the screens show everything else as normal.
//
// `bucket` asks for one point per UTC hour or day (the newest score of each) instead of the last raw
// scores; `limitPerAsset` then counts hours or days. Scoring runs every minute, so the raw view covers
// only the last ~20 minutes.
export type HistoryBucket = "hour" | "day";

export function useRiskHistory(facilityId: string, limitPerAsset = 20, bucket?: HistoryBucket) {
  const query = useQuery({
    queryKey: ["risk-history", facilityId, limitPerAsset, bucket ?? null] as const,
    queryFn: () =>
      apiFetch<RiskHistorySeries[]>(
        `/facilities/${facilityId}/risk-history?limit_per_asset=${limitPerAsset}` +
          (bucket ? `&bucket=${bucket}` : "")
      ),
    enabled: Boolean(facilityId),
    refetchInterval: useFallbackInterval(),
  });
  const byAsset = useMemo(
    () => new Map<string, RiskHistoryPoint[]>((query.data ?? []).map((s) => [s.asset_id, s.points])),
    [query.data]
  );
  return { byAsset, isError: query.isError };
}
