import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { buildHealthRows } from "../lib/assetHealth";
import { topAttention } from "../lib/attention";
import type { Asset, FloorSummary, RiskHistoryPoint, RiskScore } from "../types/api";
import { AttentionList } from "./AttentionList";

const asset = (id: string, name: string, floor: number): Asset => ({
  id,
  facility_id: "f1",
  name,
  type: "pump",
  floor_level: floor,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
});

const score = (assetId: string, value: number, over: Partial<RiskScore> = {}): RiskScore => ({
  id: `r-${assetId}`,
  facility_id: "f1",
  asset_id: assetId,
  score: value,
  rul_cycles: null,
  model_version: "v1",
  factors_json: {},
  computed_at: "2026-10-10T10:00:00Z",
  ...over,
});

const floors: FloorSummary[] = [{ level: 0, name: "Ground", map_upload_id: "u0", asset_count: 2 }];

function renderList(
  rows: ReturnType<typeof buildHealthRows>,
  limit = 5,
  lastScoredAt: string | null = null,
  unscored = 0,
  history?: Map<string, RiskHistoryPoint[]>
) {
  render(
    <MemoryRouter>
      <AttentionList
        facilityId="f1"
        attention={topAttention(rows, limit)}
        floors={floors}
        lastScoredAt={lastScoredAt}
        unscored={unscored}
        history={history}
      />
    </MemoryRouter>
  );
}

const hp = (hour: number, score: number) => ({
  computed_at: new Date(Date.UTC(2026, 9, 1, hour)).toISOString(),
  score,
  rul_cycles: null,
});

describe("AttentionList", () => {
  it("shows each machine with its verdict, why, and what to do, worst first", () => {
    renderList(
      buildHealthRows(
        [asset("a", "Aged Engine", 0), asset("b", "Warm Pump", 0)],
        [score("a", 95.8, { rul_cycles: 3.6 }), score("b", 50)]
      )
    );
    const cards = screen.getAllByRole("listitem");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Aged Engine", "Warm Pump"]);
    expect(within(cards[0]).getByText("Failing soon")).toBeInTheDocument();
    expect(within(cards[0]).getByText(/about 3\.6 cycles of life left/)).toBeInTheDocument();
    expect(within(cards[0]).getByText("Schedule maintenance now.")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Watch closely")).toBeInTheDocument();
    expect(within(cards[0]).getByText("L0")).toBeInTheDocument();
  });

  it("shows how risk moved since the previous score, for a machine that got worse", () => {
    renderList(
      buildHealthRows([asset("a", "Aged Engine", 0)], [score("a", 80)]),
      5,
      null,
      0,
      new Map([["a", [hp(8, 60), hp(11, 80)]]])
    );
    expect(screen.getByText(/▲ up 20\.0 pts in 3 h/)).toBeInTheDocument();
  });

  it("shows an improvement too, but says nothing for a steady or unknown trend", () => {
    const rows = buildHealthRows(
      [asset("a", "Better", 0), asset("b", "Steady", 0), asset("c", "NoHistory", 0)],
      [score("a", 90), score("b", 85), score("c", 80)]
    );
    renderList(
      rows,
      5,
      null,
      0,
      new Map([
        ["a", [hp(8, 99), hp(9, 90)]],
        ["b", [hp(8, 85), hp(9, 85.5)]],
      ])
    );
    expect(screen.getByText(/▼ down 9\.0 pts in 1 h/)).toBeInTheDocument();
    expect(screen.queryByText(/steady/)).not.toBeInTheDocument();
    expect(screen.queryAllByText(/pts in/)).toHaveLength(1);
  });

  it("renders fine with no history at all", () => {
    renderList(buildHealthRows([asset("a", "Aged Engine", 0)], [score("a", 80)]));
    expect(screen.getByRole("listitem", { name: "Aged Engine" })).toBeInTheDocument();
    expect(screen.queryByText(/pts in/)).not.toBeInTheDocument();
  });

  it("links Details to the Assets tab and Show on map to the asset's floor map", () => {
    renderList(buildHealthRows([asset("a", "Aged Engine", 0)], [score("a", 90)]));
    expect(screen.getByRole("link", { name: "Details" })).toHaveAttribute(
      "href",
      "/facilities/f1/assets?asset=a"
    );
    expect(screen.getByRole("link", { name: "Show on map" })).toHaveAttribute(
      "href",
      "/facilities/f1/map/u0?asset=a"
    );
  });

  it("hides Show on map when the asset's floor has no map", () => {
    renderList(buildHealthRows([asset("a", "Roof Fan", 5)], [score("a", 90)]));
    expect(screen.queryByRole("link", { name: "Show on map" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Details" })).toBeInTheDocument();
  });

  it("says how many are shown and links to the full filtered list", () => {
    const names = ["m1", "m2", "m3"];
    renderList(
      buildHealthRows(
        names.map((n) => asset(n, n, 0)),
        names.map((n, i) => score(n, 90 - i))
      ),
      2
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(/Showing 2 of 3/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See all 3 needing attention" })).toHaveAttribute(
      "href",
      "/facilities/f1/assets?show=attention"
    );
  });

  it("shows a calm all-clear (with the last scored time) when nothing needs attention", () => {
    renderList(buildHealthRows([asset("a", "Fine", 0)], [score("a", 2)]), 5, "2026-10-11T08:00:00Z");
    expect(screen.getByRole("status")).toHaveTextContent("All clear. No machine needs attention right now");
    expect(screen.getByRole("status")).toHaveTextContent("last scored");
    expect(screen.getByRole("status")).not.toHaveTextContent("not scored");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("does not let an all-clear hide machines that were never assessed", () => {
    renderList(buildHealthRows([asset("a", "Fine", 0)], [score("a", 2)]), 5, null, 2);
    expect(screen.getByRole("status")).toHaveTextContent("2 assets are not scored, so they were not assessed.");
  });

  it("uses the singular for one unscored asset", () => {
    renderList(buildHealthRows([asset("a", "Fine", 0)], [score("a", 2)]), 5, null, 1);
    expect(screen.getByRole("status")).toHaveTextContent("1 asset is not scored, so it was not assessed.");
  });
});
