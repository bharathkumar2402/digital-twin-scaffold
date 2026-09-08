import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { FacilityMapUploadStatus, FacilityMapUploadStatusResponse } from "../types/api";

const TERMINAL_STATUSES = new Set<FacilityMapUploadStatus>(["tiled", "failed", "conversion_failed"]);
const POLL_INTERVAL_MS = 2000;

// Exported standalone for direct unit testing of the stop-polling condition, without
// having to drive React Query's timers to prove it.
export function nextPollInterval(status: FacilityMapUploadStatus | undefined): number | false {
  return status && TERMINAL_STATUSES.has(status) ? false : POLL_INTERVAL_MS;
}

export function useFacilityMapUpload(facilityId: string, uploadId: string) {
  return useQuery({
    queryKey: ["facility-map-upload", facilityId, uploadId],
    queryFn: () =>
      apiFetch<FacilityMapUploadStatusResponse>(`/facilities/${facilityId}/map/${uploadId}`),
    enabled: Boolean(facilityId && uploadId),
    refetchInterval: (query) => nextPollInterval(query.state.data?.status),
  });
}
