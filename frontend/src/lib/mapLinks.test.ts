import { describe, expect, it } from "vitest";

import type { Asset, FloorSummary } from "../types/api";
import { assetDetailHref, assetMapHref } from "./mapLinks";

const asset = (floor: number): Asset => ({
  id: "a1",
  facility_id: "f1",
  name: "A",
  type: "pump",
  floor_level: floor,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
});

const floors: FloorSummary[] = [
  { level: 0, name: "Ground", map_upload_id: "u0", asset_count: 1 },
  { level: -1, name: "Basement", map_upload_id: "ub", asset_count: 0 },
];

describe("mapLinks", () => {
  it("links an asset to the map of its own floor, preselected", () => {
    expect(assetMapHref("f1", floors, asset(0))).toBe("/facilities/f1/map/u0?asset=a1");
    expect(assetMapHref("f1", floors, asset(-1))).toBe("/facilities/f1/map/ub?asset=a1");
  });

  it("returns null when the asset's floor has no finished map", () => {
    expect(assetMapHref("f1", floors, asset(3))).toBeNull();
    expect(assetMapHref("f1", [], asset(0))).toBeNull();
  });

  it("links to the Assets tab with the asset open", () => {
    expect(assetDetailHref("f1", asset(0))).toBe("/facilities/f1/assets?asset=a1");
  });
});
