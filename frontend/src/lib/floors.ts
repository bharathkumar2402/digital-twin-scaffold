// Floor logic for the multi-floor map: which floor a map belongs to, per-floor badges, searching
// and grouping assets by floor, and an asset's connections (some of which cross floors).
import type { Asset, AssetDependency, FloorSummary } from "../types/api";
import { riskBand, type AssetHealthRow } from "./assetHealth";

export function floorLabel(level: number): string {
  return level < 0 ? `B${-level}` : `L${level}`;
}

export function floorOfUpload(floors: FloorSummary[], uploadId: string): FloorSummary | undefined {
  return floors.find((floor) => floor.map_upload_id === uploadId);
}

export function assetsOnFloor<T extends { floor_level: number }>(assets: T[], level: number): T[] {
  return assets.filter((asset) => asset.floor_level === level);
}

export interface FloorBadge {
  level: number;
  name: string;
  mapUploadId: string;
  assetCount: number;
  // Assets on this floor in the "high" risk band / with a vibration alarm: the reason to go there.
  highRisk: number;
  alarms: number;
}

export function floorBadges(floors: FloorSummary[], rows: AssetHealthRow[]): FloorBadge[] {
  return floors.map((floor) => {
    const here = rows.filter((row) => row.asset.floor_level === floor.level);
    return {
      level: floor.level,
      name: floor.name,
      mapUploadId: floor.map_upload_id,
      assetCount: here.length,
      highRisk: here.filter((r) => r.risk !== null && riskBand(r.risk) === "high").length,
      alarms: here.filter((r) => r.vibration?.alarm === true).length,
    };
  });
}

export type AssetFilter = "all" | "high" | "alarm";

export function filterRows(
  rows: AssetHealthRow[],
  { query, filter }: { query: string; filter: AssetFilter }
): AssetHealthRow[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter((row) => {
    const haystack = `${row.asset.name} ${row.asset.type}`.toLowerCase().replace(/_/g, " ");
    if (!tokens.every((token) => haystack.includes(token.replace(/_/g, " ")))) return false;
    if (filter === "high") return row.risk !== null && riskBand(row.risk) === "high";
    if (filter === "alarm") return row.vibration?.alarm === true;
    return true;
  });
}

export interface FloorGroup {
  level: number;
  name: string;
  rows: AssetHealthRow[];
}

// Groups rows by floor, in the facility's floor order; a floor with no matching rows is dropped.
// Rows on a level that has no map (so no floor entry) still show, under a generic name.
export function groupByFloor(rows: AssetHealthRow[], floors: FloorSummary[]): FloorGroup[] {
  const named = new Map(floors.map((floor) => [floor.level, floor.name]));
  const levels = [...new Set([...floors.map((f) => f.level), ...rows.map((r) => r.asset.floor_level)])];
  return levels
    .sort((a, b) => a - b)
    .map((level) => ({
      level,
      name: named.get(level) ?? `Level ${level}`,
      rows: rows.filter((row) => row.asset.floor_level === level),
    }))
    .filter((group) => group.rows.length > 0);
}

export interface Connection {
  asset: Asset;
  dependencyId: string;
}

export interface Connections {
  // Upstream: this asset stops if one of these stops.
  dependsOn: Connection[];
  // Downstream: these stop if this asset stops.
  dependedOnBy: Connection[];
}

// parent depends on child (see AssetDependency): "depends on" is where this asset is the parent.
export function connectionsOf(
  assetId: string,
  dependencies: AssetDependency[],
  assetsById: Map<string, Asset>
): Connections {
  const resolve = (otherId: string, dependencyId: string): Connection[] => {
    const asset = assetsById.get(otherId);
    return asset ? [{ asset, dependencyId }] : []; // a link to a deleted asset is not shown
  };
  return {
    dependsOn: dependencies
      .filter((dep) => dep.parent_asset_id === assetId)
      .flatMap((dep) => resolve(dep.child_asset_id, dep.id)),
    dependedOnBy: dependencies
      .filter((dep) => dep.child_asset_id === assetId)
      .flatMap((dep) => resolve(dep.parent_asset_id, dep.id)),
  };
}
