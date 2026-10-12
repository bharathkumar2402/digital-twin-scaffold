import { useMemo } from "react";

import { buildHealthRows, floorOptions, summarizeByFloor } from "../lib/assetHealth";
import { useAssets } from "./useAssets";
import { useFacilities } from "./useFacilities";
import { useRiskScores } from "./useRiskScores";
import { useTelemetryFreshness } from "./useTelemetryFreshness";

export interface LoadFailure {
  what: string;
  retry: () => void;
}

// The facility's assets joined with their latest scores, plus the floors they sit on. Shared by
// the Overview and Assets tabs so both read the same data the same way.
export function useFacilityHealth(facilityId: string) {
  const facilities = useFacilities();
  const facility = facilities.data?.find((f) => f.id === facilityId) ?? null;
  const assetsQuery = useAssets(facilityId);
  const scoresQuery = useRiskScores(facilityId);
  // Not required to render: until it answers (or if it fails) no asset is claimed to be silent.
  const freshnessQuery = useTelemetryFreshness(facilityId);

  const allRows = useMemo(
    () => buildHealthRows(assetsQuery.data ?? [], scoresQuery.data ?? [], freshnessQuery.data),
    [assetsQuery.data, scoresQuery.data, freshnessQuery.data]
  );
  const options = useMemo(() => floorOptions(allRows, facility?.floors ?? []), [allRows, facility]);
  const floorHealth = useMemo(() => summarizeByFloor(allRows, options), [allRows, options]);
  const assetsById = useMemo(
    () => new Map((assetsQuery.data ?? []).map((asset) => [asset.id, asset])),
    [assetsQuery.data]
  );

  const failed: LoadFailure[] = [];
  if (assetsQuery.isError) failed.push({ what: "assets", retry: () => void assetsQuery.refetch() });
  if (scoresQuery.isError) failed.push({ what: "risk scores", retry: () => void scoresQuery.refetch() });

  return {
    facility,
    allRows,
    options,
    floorHealth,
    assetsById,
    failed,
    // Both queries have answered successfully.
    loaded: assetsQuery.data !== undefined && scoresQuery.data !== undefined,
    scoreCount: scoresQuery.data?.length ?? 0,
  };
}

export type FacilityHealth = ReturnType<typeof useFacilityHealth>;
