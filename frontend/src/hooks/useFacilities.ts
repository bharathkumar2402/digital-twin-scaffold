import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { FacilitySummary } from "../types/api";

// The caller's tenant's facilities, each with its newest finished floor-plan upload (if any).
export function useFacilities() {
  return useQuery({
    queryKey: ["facilities"] as const,
    queryFn: () => apiFetch<FacilitySummary[]>("/facilities"),
  });
}
