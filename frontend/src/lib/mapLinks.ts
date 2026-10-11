import type { Asset, FloorSummary } from "../types/api";

// Each floor has its own map, so an asset is shown on the map of the floor it is on. Returns null
// when that floor has no finished map yet.
export function assetMapHref(facilityId: string, floors: FloorSummary[], asset: Asset): string | null {
  const floor = floors.find((f) => f.level === asset.floor_level);
  return floor ? `/facilities/${facilityId}/map/${floor.map_upload_id}?asset=${asset.id}` : null;
}

// The Assets tab with this asset's panel open.
export function assetDetailHref(facilityId: string, asset: Asset): string {
  return `/facilities/${facilityId}/assets?asset=${asset.id}`;
}
