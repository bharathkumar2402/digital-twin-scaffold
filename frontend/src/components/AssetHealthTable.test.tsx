import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildHealthRows } from "../lib/assetHealth";
import type { Asset, RiskScore } from "../types/api";
import { AssetHealthTable } from "./AssetHealthTable";

function asset(id: string, name: string): Asset {
  return {
    id,
    facility_id: "f1",
    name,
    type: "engine",
    x: 0,
    y: 0,
    status: "operational",
    installed_date: null,
    manufacturer: null,
    model: null,
  };
}

function score(assetId: string, value: number, extra: Partial<RiskScore> = {}): RiskScore {
  return {
    id: `r-${assetId}`,
    facility_id: "f1",
    asset_id: assetId,
    score: value,
    rul_cycles: null,
    model_version: "v1",
    factors_json: {},
    computed_at: "2026-10-10T10:00:00Z",
    ...extra,
  };
}

function renderTable(
  assets: Asset[],
  scores: RiskScore[],
  props: { selected?: string | null; onSelect?: (id: string) => void } = {}
) {
  render(
    <AssetHealthTable
      rows={buildHealthRows(assets, scores)}
      selectedAssetId={props.selected ?? null}
      onSelect={props.onSelect ?? (() => undefined)}
    />
  );
}

const aged = asset("a", "Compressor Engine A (aged)");
const fresh = asset("b", "Compressor Engine B (new)");
const lonely = asset("c", "Lonely Pump");

describe("AssetHealthTable", () => {
  it("shows risk, RUL and the vibration alarm, worst asset first", () => {
    renderTable(
      [fresh, aged],
      [
        score("b", 0.4, { rul_cycles: 115.9, factors_json: { vibration_alarm: 0 } }),
        score("a", 95.8, {
          rul_cycles: 3.6,
          factors_json: { vibration_alarm: 1, vibration_alarm_channels: 2, vibration_channels_scored: 2 },
        }),
      ]
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Compressor Engine A (aged)")).toBeInTheDocument();
    expect(within(rows[0]).getByText("95.8")).toBeInTheDocument();
    expect(within(rows[0]).getByText("3.6")).toBeInTheDocument();
    expect(within(rows[0]).getByText(/ALARM \(2\/2 ch\)/)).toBeInTheDocument();
    expect(within(rows[1]).getByText("115.9")).toBeInTheDocument();
    expect(within(rows[1]).getByText("normal")).toBeInTheDocument();
  });

  it("renders missing RUL as a dash, never as zero", () => {
    renderTable([aged], [score("a", 50, { rul_cycles: null })]);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).queryByText("0.0")).not.toBeInTheDocument();
  });

  it("shows a real zero-cycle RUL as 0.0", () => {
    renderTable([aged], [score("a", 99, { rul_cycles: 0 })]);
    expect(screen.getByText("0.0")).toBeInTheDocument();
  });

  it("says 'not scored' for an asset with no risk row, not a low-risk number", () => {
    renderTable([lonely], []);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("not scored")).toBeInTheDocument();
    expect(within(row).getByText("no data")).toBeInTheDocument();
    expect(within(row).queryByText(/low/)).not.toBeInTheDocument();
  });

  it("explains why vibration is missing when the reason is known", () => {
    renderTable(
      [aged],
      [score("a", 10, { factors_json: { vibration_unavailable_reason: "invalid_waveform" } })]
    );
    expect(screen.getByText("no data")).toHaveAttribute("title", "invalid_waveform");
  });

  it("selects an asset on click and on keyboard activation", () => {
    const onSelect = vi.fn();
    renderTable([aged, fresh], [score("a", 90), score("b", 5)], { onSelect });
    const rows = screen.getAllByRole("row").slice(1);
    fireEvent.click(rows[0]);
    expect(onSelect).toHaveBeenLastCalledWith("a");
    fireEvent.keyDown(rows[1], { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith("b");
  });

  it("marks the selected row", () => {
    renderTable([aged], [score("a", 90)], { selected: "a" });
    expect(screen.getAllByRole("row")[1]).toHaveAttribute("aria-selected", "true");
  });

  it("handles an empty facility", () => {
    renderTable([], []);
    expect(screen.getByText(/no assets yet/i)).toBeInTheDocument();
  });
});
