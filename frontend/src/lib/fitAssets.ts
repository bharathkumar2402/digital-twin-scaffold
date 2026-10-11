import { pixelToLngLat } from "./mapCoords";
import type { Asset } from "../types/api";

export type LngLatBoundsTuple = [[number, number], [number, number]];

// The box (south-west, north-east) that contains every asset, so the map can open framed on
// the equipment instead of on the whole world. Null when there is nothing to frame.
export function assetBounds(assets: Pick<Asset, "x" | "y">[]): LngLatBoundsTuple | null {
  if (assets.length === 0) return null;
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const asset of assets) {
    const [lng, lat] = pixelToLngLat(asset.x, asset.y);
    west = Math.min(west, lng);
    east = Math.max(east, lng);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  }
  return [
    [west, south],
    [east, north],
  ];
}
