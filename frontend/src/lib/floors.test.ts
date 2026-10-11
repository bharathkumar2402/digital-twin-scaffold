import { describe, expect, it } from "vitest";

import type { Asset, AssetDependency, FloorSummary, RiskScore } from "../types/api";
import { buildHealthRows } from "./assetHealth";
import {
  assetsOnFloor,
  connectionsOf,
  filterRows,
  floorBadges,
  floorLabel,
  floorOfUpload,
  groupByFloor,
} from "./floors";

const floors: FloorSummary[] = [
  { level: 0, name: "Ground floor", map_upload_id: "u0", asset_count: 2 },
  { level: 1, name: "First floor", map_upload_id: "u1", asset_count: 1 },
  { level: 2, name: "Roof", map_upload_id: "u2", asset_count: 0 },
];

function asset(id: string, name: string, floor: number, type = "pump"): Asset {
  return {
    id,
    facility_id: "f",
    name,
    type,
    floor_level: floor,
    x: 0,
    y: 0,
    status: "operational",
    installed_date: null,
    manufacturer: null,
    model: null,
  };
}

function score(assetId: string, value: number, alarm = false): RiskScore {
  return {
    id: `r-${assetId}`,
    facility_id: "f",
    asset_id: assetId,
    score: value,
    rul_cycles: null,
    model_version: "v",
    factors_json: { vibration_alarm: alarm ? 1 : 0 },
    computed_at: "2026-10-10T10:00:00Z",
  };
}

const assets = [
  asset("a", "Air Compressor AC-1", 0, "air_compressor"),
  asset("b", "Chiller CH-1", 0, "chiller"),
  asset("c", "Robot Cell R-1", 1, "robot_cell"),
];
const rows = buildHealthRows(assets, [score("a", 95, true), score("b", 3), score("c", 80)]);

describe("floor basics", () => {
  it("labels ground and upper floors L<n> and basements B<n>", () => {
    expect([floorLabel(0), floorLabel(2), floorLabel(-1)]).toEqual(["L0", "L2", "B1"]);
  });

  it("finds which floor a map upload belongs to, or nothing for an unknown upload", () => {
    expect(floorOfUpload(floors, "u1")?.name).toBe("First floor");
    expect(floorOfUpload(floors, "nope")).toBeUndefined();
    expect(floorOfUpload([], "u0")).toBeUndefined();
  });

  it("keeps only the assets on a floor", () => {
    expect(assetsOnFloor(assets, 0).map((a) => a.id)).toEqual(["a", "b"]);
    expect(assetsOnFloor(assets, 2)).toEqual([]);
  });
});

describe("floorBadges", () => {
  it("counts assets, high-risk assets and vibration alarms per floor", () => {
    const badges = floorBadges(floors, rows);
    expect(badges.map((b) => [b.level, b.assetCount, b.highRisk, b.alarms])).toEqual([
      [0, 2, 1, 1],
      [1, 1, 1, 0],
      [2, 0, 0, 0],
    ]);
    expect(badges[0].mapUploadId).toBe("u0");
  });

  it("does not call an unscored asset high risk", () => {
    const unscored = buildHealthRows([asset("x", "X", 0)], []);
    expect(floorBadges(floors, unscored)[0].highRisk).toBe(0);
  });
});

describe("filterRows", () => {
  it("matches every search word against the name and type, ignoring case and underscores", () => {
    expect(filterRows(rows, { query: "air comp", filter: "all" }).map((r) => r.asset.id)).toEqual(["a"]);
    expect(filterRows(rows, { query: "ROBOT_CELL", filter: "all" }).map((r) => r.asset.id)).toEqual(["c"]);
    expect(filterRows(rows, { query: "chiller nothing", filter: "all" })).toEqual([]);
    expect(filterRows(rows, { query: "  ", filter: "all" })).toHaveLength(3);
  });

  it("filters to high-risk and to alarming assets", () => {
    expect(filterRows(rows, { query: "", filter: "high" }).map((r) => r.asset.id).sort()).toEqual(["a", "c"]);
    expect(filterRows(rows, { query: "", filter: "alarm" }).map((r) => r.asset.id)).toEqual(["a"]);
  });

  it("combines search and filter", () => {
    expect(filterRows(rows, { query: "robot", filter: "alarm" })).toEqual([]);
  });
});

describe("groupByFloor", () => {
  it("groups in floor order, naming each floor and dropping empty ones", () => {
    const groups = groupByFloor(rows, floors);
    expect(groups.map((g) => [g.level, g.name, g.rows.length])).toEqual([
      [0, "Ground floor", 2],
      [1, "First floor", 1],
    ]);
  });

  it("still shows assets on a level that has no map, under a generic name", () => {
    const stray = buildHealthRows([asset("z", "Stray", 7)], []);
    expect(groupByFloor(stray, floors).map((g) => [g.level, g.name])).toEqual([[7, "Level 7"]]);
  });

  it("falls back to levels from the rows when the facility has no floor list", () => {
    expect(groupByFloor(rows, []).map((g) => g.level)).toEqual([0, 1]);
  });
});

describe("connectionsOf", () => {
  const byId = new Map(assets.map((a) => [a.id, a]));
  const deps: AssetDependency[] = [
    { id: "d1", facility_id: "f", parent_asset_id: "c", child_asset_id: "a" }, // R-1 depends on AC-1
    { id: "d2", facility_id: "f", parent_asset_id: "b", child_asset_id: "a" }, // CH-1 depends on AC-1
    { id: "d3", facility_id: "f", parent_asset_id: "a", child_asset_id: "ghost" }, // deleted upstream
  ];

  it("separates upstream from downstream, and keeps cross-floor links", () => {
    const ac1 = connectionsOf("a", deps, byId);
    expect(ac1.dependedOnBy.map((c) => c.asset.id).sort()).toEqual(["b", "c"]);
    expect(ac1.dependedOnBy.find((c) => c.asset.id === "c")?.asset.floor_level).toBe(1);
    const r1 = connectionsOf("c", deps, byId);
    expect(r1.dependsOn.map((c) => c.asset.id)).toEqual(["a"]);
    expect(r1.dependedOnBy).toEqual([]);
  });

  it("does not show a link to an asset that no longer exists", () => {
    expect(connectionsOf("a", deps, byId).dependsOn).toEqual([]);
  });
});
