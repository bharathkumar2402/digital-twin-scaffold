import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildHealthRows } from "../lib/assetHealth";
import type { Asset, FloorSummary, RiskScore } from "../types/api";
import { AssetBrowser } from "./AssetBrowser";

const floors: FloorSummary[] = [
  { level: 0, name: "Ground floor", map_upload_id: "u0", asset_count: 2 },
  { level: 1, name: "First floor", map_upload_id: "u1", asset_count: 1 },
];

function asset(id: string, name: string, floor: number, type: string): Asset {
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

function score(assetId: string, value: number, alarm: boolean): RiskScore {
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
const rows = buildHealthRows(assets, [score("a", 95, true), score("b", 3, false), score("c", 80, false)]);

function renderBrowser(onPick = vi.fn(), currentLevel: number | undefined = 1) {
  render(<AssetBrowser rows={rows} floors={floors} currentLevel={currentLevel} isLoading={false} onPick={onPick} />);
  return onPick;
}

describe("AssetBrowser", () => {
  it("groups assets by floor and lists the floor being viewed first", () => {
    renderBrowser();
    const sections = screen.getAllByRole("region");
    expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual(["First floor", "Ground floor"]);
    expect(within(sections[1]).getAllByRole("button")).toHaveLength(2);
  });

  it("searches by name and says how many match", () => {
    renderBrowser();
    fireEvent.change(screen.getByLabelText("Search assets"), { target: { value: "robot" } });
    expect(screen.getByText("Robot Cell R-1")).toBeInTheDocument();
    expect(screen.queryByText("Chiller CH-1")).not.toBeInTheDocument();
    expect(screen.getByText("1 of 3 match")).toBeInTheDocument();
  });

  it("filters to high-risk assets and to vibration alarms, and says when nothing matches", () => {
    renderBrowser();
    fireEvent.click(screen.getByRole("button", { name: "High risk" }));
    expect(screen.queryByText("Chiller CH-1")).not.toBeInTheDocument();
    expect(screen.getByText("Air Compressor AC-1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Vibration alarm" }));
    expect(screen.queryByText("Robot Cell R-1")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search assets"), { target: { value: "zzz" } });
    expect(screen.getByText("No asset matches that search.")).toBeInTheDocument();
  });

  it("hands the picked asset to the caller (which switches floors when needed)", () => {
    const onPick = renderBrowser();
    fireEvent.click(screen.getByText("Chiller CH-1"));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: "b", floor_level: 0 }));
  });
});
