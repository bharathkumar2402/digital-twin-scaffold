import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { AssetDependency, AssetDependencyCreateRequest } from "../types/api";

function dependenciesQueryKey(facilityId: string) {
  return ["asset-dependencies", facilityId] as const;
}

export function useAssetDependencies(facilityId: string) {
  return useQuery({
    queryKey: dependenciesQueryKey(facilityId),
    queryFn: () => apiFetch<AssetDependency[]>(`/facilities/${facilityId}/asset-dependencies`),
    enabled: Boolean(facilityId),
  });
}

export function useCreateAssetDependency(facilityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AssetDependencyCreateRequest) =>
      apiFetch<AssetDependency>(`/facilities/${facilityId}/asset-dependencies`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: dependenciesQueryKey(facilityId) });
    },
  });
}

export function useDeleteAssetDependency(facilityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dependencyId: string) =>
      apiFetch<void>(`/facilities/${facilityId}/asset-dependencies/${dependencyId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: dependenciesQueryKey(facilityId) });
    },
  });
}
