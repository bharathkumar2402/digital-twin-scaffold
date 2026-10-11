import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { buildHealthRows } from "../lib/assetHealth";
import type { Asset, AssetDependency, RiskScore } from "../types/api";
import { AssetPanel, type AssetPanelEdit } from "./AssetPanel";

vi.mock("../hooks/useAssetTelemetry", () => ({ useAssetTelemetry: vi.fn() }));

const asset = (id: string, name: string, floor = 0): Asset => ({
  id,
  facility_id: "f1",
  name,
  type: "pump",
  floor_level: floor,
  x: 12.5,
  y: 3,
  status: "operational",
  installed_date: "2020-01-02",
  manufacturer: "Acme",
  model: "P-9",
});

const pump = asset("a", "Feed Pump");
const valve = asset("b", "Upstairs Valve", 1);
const assetsById = new Map([pump, valve].map((a) => [a.id, a]));
const dependencies: AssetDependency[] = [
  { id: "d1", facility_id: "f1", parent_asset_id: "a", child_asset_id: "b" },
];

const score: RiskScore = {
  id: "r",
  facility_id: "f1",
  asset_id: "a",
  score: 80,
  rul_cycles: 12,
  model_version: "risk-v1",
  factors_json: {
    rul_model_version: "rul-v2",
    rul_dataset_name: "C-MAPSS",
    vibration_alarm: 1,
    vibration_max_score: 0.7,
    vibration_threshold: 0.5,
  },
  computed_at: "2026-10-10T10:00:00Z",
};

function renderPanel(
  props: {
    edit?: AssetPanelEdit;
    mapHref?: string | null;
    scored?: boolean;
    onJump?: () => void;
    history?: { computed_at: string; score: number; rul_cycles: number | null }[];
  } = {}
) {
  const row = buildHealthRows([pump], props.scored === false ? [] : [score])[0];
  render(
    <MemoryRouter>
      <AssetPanel
        facilityId="f1"
        row={row}
        dependencies={dependencies}
        assetsById={assetsById}
        currentLevel={0}
        onJump={props.onJump ?? vi.fn()}
        onClose={vi.fn()}
        mapHref={props.mapHref}
        edit={props.edit}
        history={props.history}
      />
    </MemoryRouter>
  );
}

const edit = (over: Partial<AssetPanelEdit> = {}): AssetPanelEdit => ({
  isSaving: false,
  isDeleting: false,
  isDeletingDependency: false,
  onUpdate: vi.fn(),
  onDelete: vi.fn(),
  onDeleteDependency: vi.fn(),
  ...over,
});

describe("AssetPanel", () => {
  beforeEach(() => {
    vi.mocked(useAssetTelemetry).mockReturnValue({
      data: [],
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useAssetTelemetry>);
  });

  it("leads with the health verdict, then shows the numbers with their model and dataset", () => {
    renderPanel();
    const panel = screen.getByRole("complementary", { name: "Details for Feed Pump" });
    expect(panel).toHaveFocus();
    expect(within(panel).getByText("Failing soon")).toBeInTheDocument();
    expect(within(panel).getByText("Schedule maintenance now.")).toBeInTheDocument();
    expect(within(panel).getByText("80.0 (risk model risk-v1)")).toBeInTheDocument();
    expect(within(panel).getByText("12.0 cycles (rul-v2, C-MAPSS)")).toBeInTheDocument();
    expect(within(panel).getByText(/ALARM – peak score 0.70 vs threshold 0.50/)).toBeInTheDocument();
    expect(within(panel).getByText("Acme")).toBeInTheDocument();
    expect(within(panel).getByText(/pump · L0/)).toBeInTheDocument();
  });

  it("shows a risk trend with a sparkline and the change, when there is history", () => {
    renderPanel({
      history: [
        { computed_at: "2026-10-01T08:00:00Z", score: 55, rul_cycles: null },
        { computed_at: "2026-10-01T11:00:00Z", score: 80, rul_cycles: null },
      ],
    });
    expect(screen.getByText("Risk trend")).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: /Risk over the last 2 scores: from 55\.0 to 80\.0/ })
    ).toBeInTheDocument();
    expect(screen.getByText(/▲ up 25\.0 pts in 3 h/)).toBeInTheDocument();
  });

  it("omits the trend section entirely when there is no history (or it failed to load)", () => {
    renderPanel();
    expect(screen.queryByText("Risk trend")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /Risk over the last/ })).not.toBeInTheDocument();
  });

  it("with a single past score, says so instead of drawing a trend", () => {
    renderPanel({ history: [{ computed_at: "2026-10-01T08:00:00Z", score: 55, rul_cycles: null }] });
    expect(screen.getByText("Only one score so far (55.0).")).toBeInTheDocument();
    expect(screen.queryByText(/pts in/)).not.toBeInTheDocument();
  });

  it("says plainly when the asset has not been scored", () => {
    renderPanel({ scored: false });
    expect(screen.getByText("Not enough data")).toBeInTheDocument();
    expect(screen.getByText("not scored")).toBeInTheDocument();
    expect(screen.getByText("no data")).toBeInTheDocument();
  });

  it("is read-only without edit handlers: no state change, delete or unlink", () => {
    renderPanel();
    expect(screen.queryByLabelText("Change operating state")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete asset" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unlink" })).not.toBeInTheDocument();
    // but connections are still shown and navigable
    expect(screen.getByRole("button", { name: "Upstairs Valve" })).toBeInTheDocument();
  });

  it("with edit handlers, changes state, unlinks and deletes through them", () => {
    const handlers = edit();
    renderPanel({ edit: handlers });
    fireEvent.change(screen.getByLabelText("Change operating state"), { target: { value: "maintenance" } });
    expect(handlers.onUpdate).toHaveBeenCalledWith({ status: "maintenance" });
    fireEvent.click(screen.getByRole("button", { name: "Unlink" }));
    expect(handlers.onDeleteDependency).toHaveBeenCalledWith("d1");
    fireEvent.click(screen.getByRole("button", { name: "Delete asset" }));
    expect(handlers.onDelete).toHaveBeenCalledTimes(1);
  });

  it("disables delete while deleting and unlink while unlinking", () => {
    renderPanel({ edit: edit({ isDeleting: true, isDeletingDependency: true }) });
    expect(screen.getByRole("button", { name: "Deleting..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Unlink" })).toBeDisabled();
  });

  it("tags a connection on another floor and jumps to it", () => {
    const onJump = vi.fn();
    renderPanel({ onJump });
    expect(screen.getByText("L1")).toHaveAttribute("title", expect.stringContaining("another floor"));
    fireEvent.click(screen.getByRole("button", { name: "Upstairs Valve" }));
    expect(onJump).toHaveBeenCalledWith(valve);
  });

  it("offers Show on map only when given a destination", () => {
    renderPanel({ mapHref: "/facilities/f1/map/u0?asset=a" });
    expect(screen.getByRole("link", { name: "Show on map" })).toHaveAttribute(
      "href",
      "/facilities/f1/map/u0?asset=a"
    );
  });

  it("shows a telemetry error instead of an empty chart", () => {
    vi.mocked(useAssetTelemetry).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
    } as unknown as ReturnType<typeof useAssetTelemetry>);
    renderPanel();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load telemetry");
  });
});
