import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { Asset, AssetCreateRequest, AssetUpdateRequest } from "../types/api";

function assetsQueryKey(facilityId: string) {
  return ["assets", facilityId] as const;
}

export function useAssets(facilityId: string) {
  return useQuery({
    queryKey: assetsQueryKey(facilityId),
    queryFn: () => apiFetch<Asset[]>(`/facilities/${facilityId}/assets`),
    enabled: Boolean(facilityId),
  });
}

export function useCreateAsset(facilityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AssetCreateRequest) =>
      apiFetch<Asset>(`/facilities/${facilityId}/assets`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: assetsQueryKey(facilityId) });
    },
  });
}

export function useUpdateAsset(facilityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ assetId, body }: { assetId: string; body: AssetUpdateRequest }) =>
      apiFetch<Asset>(`/facilities/${facilityId}/assets/${assetId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: assetsQueryKey(facilityId) });
    },
  });
}

export function useDeleteAsset(facilityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (assetId: string) =>
      apiFetch<void>(`/facilities/${facilityId}/assets/${assetId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: assetsQueryKey(facilityId) });
    },
  });
}
