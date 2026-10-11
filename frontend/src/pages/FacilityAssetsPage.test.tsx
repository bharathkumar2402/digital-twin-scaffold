import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAssetDependencies } from "../hooks/useAssetDependencies";
import { useAssets } from "../hooks/useAssets";
import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { useRiskHistory } from "../hooks/useRiskHistory";
import { useRiskScores } from "../hooks/useRiskScores";
import type { Asset, RiskScore } from "../types/api";
import { a11yViolations, headingLevels, outlineProblems } from "../test/a11y";
import { FacilityAssetsPage } from "./FacilityAssetsPage";

vi.mock("../hooks/useAssets", () => ({ useAssets: vi.fn() }));
vi.mock("../hooks/useAssetDependencies", () => ({ useAssetDependencies: vi.fn() }));
vi.mock("../hooks/useRiskScores", () => ({ useRiskScores: vi.fn() }));
vi.mock("../hooks/useRiskHistory", () => ({ useRiskHistory: vi.fn() }));
vi.mock("../hooks/useAssetTelemetry", () => ({ useAssetTelemetry: vi.fn() }));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));
function LocationProbe(): React.JSX.Element {
  const { search } = useLocation();
  return <div data-testid="search">{search}</div>;
}

const asset = (id: string, name: string, floor = 0): Asset => ({
  id,
  facility_id: "f1",
  name,
  type: "engine",
  floor_level: floor,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
});

const score = (assetId: string, value: number, rul: number | null): RiskScore => ({
  id: `r-${assetId}`,
  facility_id: "f1",
  asset_id: assetId,
  score: value,
  rul_cycles: rul,
  model_version: "risk-v1",
  factors_json: { vibration_alarm: 1, vibration_max_score: 0.73, vibration_threshold: 0.55 },
  computed_at: "2026-10-10T10:00:00Z",
});

function query<T>(over: Record<string, unknown>) {
  return { data: undefined, isPending: false, isError: false, ...over } as T;
}

function renderPage(url = "/facilities/f1/assets?upload=u9") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/facilities/:facilityId/assets" element={<FacilityAssetsPage />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>
  );
}

describe("FacilityAssetsPage", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ role: "facility_manager", logout: vi.fn() } as unknown as ReturnType<
      typeof useAuth
    >);
    vi.mocked(useFacilities).mockReturnValue(
      query<ReturnType<typeof useFacilities>>({
        data: [{ id: "f1", name: "Demo Plant 1", latest_map_upload_id: "u9", asset_count: 2, floors: [] }],
      })
    );
    vi.mocked(useAssets).mockReturnValue(
      query<ReturnType<typeof useAssets>>({ data: [asset("a", "Aged Engine"), asset("b", "New Engine")] })
    );
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({ data: [score("a", 95.8, 3.6), score("b", 0.4, 115.9)] })
    );
    vi.mocked(useRiskHistory).mockReturnValue({ byAsset: new Map(), isError: false });
    vi.mocked(useAssetTelemetry).mockReturnValue(
      query<ReturnType<typeof useAssetTelemetry>>({ data: [] })
    );
    vi.mocked(useAssetDependencies).mockReturnValue(
      query<ReturnType<typeof useAssetDependencies>>({ data: [] })
    );
  });

  it("shows the health table with a status per asset, and nothing from other tabs", () => {
    renderPage();
    expect(screen.getByRole("region", { name: "Asset health" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Asset health" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Facility summary" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "About these models" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Agent analysis" })).not.toBeInTheDocument();
  });

  describe("opening an asset from a link (?asset=)", () => {
    it("opens that asset's panel and consumes the param so a reload does not reopen it", () => {
      renderPage("/facilities/f1/assets?asset=b");
      expect(screen.getByRole("complementary", { name: "Details for New Engine" })).toBeInTheDocument();
      expect(screen.getByTestId("search")).not.toHaveTextContent("asset=");
    });

    it("ignores an id that is not one of this facility's assets, and still clears it", () => {
      renderPage("/facilities/f1/assets?asset=does-not-exist");
      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
      expect(screen.getByRole("table", { name: "Asset health" })).toBeInTheDocument();
      expect(screen.getByTestId("search")).not.toHaveTextContent("asset=");
    });

    it("drops the floor and quick filters so the asset is actually visible", () => {
      renderPage("/facilities/f1/assets?floor=0&show=alarms&asset=a");
      expect(screen.getByRole("complementary", { name: "Details for Aged Engine" })).toBeInTheDocument();
      const search = screen.getByTestId("search").textContent ?? "";
      expect(search).not.toContain("floor=");
      expect(search).not.toContain("show=");
    });
  });

  describe("search and quick filters", () => {
    const rowNames = () =>
      within(screen.getByRole("table", { name: "Asset health" }))
        .getAllByRole("button")
        .map((b) => b.textContent);

    it("searches by name, case-insensitively", () => {
      renderPage();
      fireEvent.change(screen.getByLabelText("Search assets"), { target: { value: "aGeD" } });
      expect(rowNames()).toEqual(["Aged Engine"]);
    });

    it("filters to assets that need attention and writes ?show= to the URL", () => {
      // New Engine: low risk and no vibration alarm, so genuinely healthy.
      vi.mocked(useRiskScores).mockReturnValue(
        query<ReturnType<typeof useRiskScores>>({
          data: [score("a", 95.8, 3.6), { ...score("b", 0.4, 115.9), factors_json: {} }],
        })
      );
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Needs attention" }));
      expect(rowNames()).toEqual(["Aged Engine"]);
      expect(screen.getByTestId("search")).toHaveTextContent("show=attention");
      expect(screen.getByRole("button", { name: "Needs attention" })).toHaveAttribute("aria-pressed", "true");
    });

    it("restores a quick filter from the URL and ignores an unknown one", () => {
      renderPage("/facilities/f1/assets?show=alarms");
      expect(rowNames()).toEqual(["Aged Engine", "New Engine"]);
      cleanup();
      renderPage("/facilities/f1/assets?show=nonsense");
      expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    });

    it("shows 'No assets match' with a way back when nothing matches", () => {
      renderPage();
      fireEvent.change(screen.getByLabelText("Search assets"), { target: { value: "zzz" } });
      expect(screen.getByText("No assets match")).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
      expect(rowNames()).toEqual(["Aged Engine", "New Engine"]);
    });

    it("closes the panel when the selected asset no longer matches the search", () => {
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
      expect(screen.getByRole("complementary")).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Search assets"), { target: { value: "new" } });
      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    });
  });

  it("opens the detail drawer for a clicked asset with its RUL and vibration provenance", () => {
    renderPage();
    const table = screen.getByRole("table", { name: "Asset health" });
    fireEvent.click(within(table).getByText("Aged Engine"));
    const drawer = screen.getByRole("complementary", { name: "Details for Aged Engine" });
    expect(within(drawer).getByText(/ALARM/)).toBeInTheDocument();
    expect(within(drawer).getByText(/peak score 0.73 vs threshold 0.55/)).toBeInTheDocument();
    expect(within(drawer).getByText(/3\.6 cycles \(/)).toBeInTheDocument();
    // plain-language verdict first: what it is, why, and what to do
    expect(within(drawer).getByText("Failing soon")).toBeInTheDocument();
    expect(within(drawer).getByText("Schedule maintenance now.")).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("passes the selected asset's score history to the panel (and only that asset's)", () => {
    vi.mocked(useRiskHistory).mockReturnValue({
      byAsset: new Map([
        [
          "a",
          [
            { computed_at: "2026-10-01T08:00:00Z", score: 50, rul_cycles: null },
            { computed_at: "2026-10-01T09:00:00Z", score: 95.8, rul_cycles: 3.6 },
          ],
        ],
        ["b", [{ computed_at: "2026-10-01T08:00:00Z", score: 1, rul_cycles: null }]],
      ]),
      isError: false,
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
    const panel = screen.getByRole("complementary", { name: "Details for Aged Engine" });
    expect(within(panel).getByText(/▲ up 45\.8 pts in 1 h/)).toBeInTheDocument();
    expect(within(panel).queryByText(/Only one score/)).not.toBeInTheDocument();
  });

  it("shows a skeleton (busy, no table) while the data loads", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ isPending: true }));
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ isPending: true }));
    renderPage();
    expect(screen.getByRole("status", { name: "Loading results" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("surfaces a load failure naming what failed, and Retry refetches only that query", () => {
    const refetch = vi.fn();
    const assetsRefetch = vi.fn();
    vi.mocked(useAssets).mockReturnValue(
      query<ReturnType<typeof useAssets>>({ data: [asset("a", "Aged Engine")], refetch: assetsRefetch })
    );
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({ isError: true, data: undefined, refetch })
    );
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load this facility’s risk scores");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(assetsRefetch).not.toHaveBeenCalled();
  });

  it("shows an error, not a skeleton, when one query failed and the other is still pending", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ isPending: true }));
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ isError: true }));
    renderPage();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Loading results" })).not.toBeInTheDocument();
  });

  it("explains an empty facility and links to the map", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ data: [] }));
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ data: [] }));
    renderPage();
    expect(screen.getByText("No assets in this facility yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the map" })).toHaveAttribute("href", "/facilities/f1/map/u9");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says scoring has not run when assets exist but there are no scores at all", () => {
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ data: [] }));
    renderPage();
    expect(screen.getByText("No risk scores yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  describe("floor filter", () => {
    beforeEach(() => {
      vi.mocked(useFacilities).mockReturnValue(
        query<ReturnType<typeof useFacilities>>({
          data: [
            {
              id: "f1",
              name: "Meridian",
              latest_map_upload_id: "u9",
              asset_count: 3,
              floors: [
                { level: 0, name: "Ground", map_upload_id: "u0", asset_count: 2 },
                { level: 1, name: "Mezzanine", map_upload_id: "u1", asset_count: 1 },
                { level: 2, name: "Roof", map_upload_id: "u2", asset_count: 0 },
              ],
            },
          ],
        })
      );
      vi.mocked(useAssets).mockReturnValue(
        query<ReturnType<typeof useAssets>>({
          data: [asset("a", "Ground Press", 0), asset("b", "Ground Lathe", 0), asset("c", "Mezz Pump", 1)],
        })
      );
      vi.mocked(useRiskScores).mockReturnValue(
        query<ReturnType<typeof useRiskScores>>({
          data: [score("a", 95.8, 3.6), score("b", 10, 90), score("c", 50, 40)],
        })
      );
    });

    const tableNames = () =>
      within(screen.getByRole("table", { name: "Asset health" }))
        .getAllByRole("button")
        .map((b) => b.textContent);

    it("offers all floors plus each floor with its own counts", () => {
      renderPage("/facilities/f1/assets");
      const group = screen.getByRole("group", { name: "Filter by floor" });
      expect(within(group).getByRole("button", { name: /All floors/ })).toHaveAttribute("aria-pressed", "true");
      expect(within(group).getByRole("button", { name: /Ground.*2 assets.*1 high risk/ })).toBeInTheDocument();
      expect(within(group).getByRole("button", { name: /Mezzanine.*1 asset\b.*0 high risk/ })).toBeInTheDocument();
      expect(tableNames()).toHaveLength(3);
    });

    it("filters the table to the chosen floor and writes ?floor= to the URL", () => {
      renderPage("/facilities/f1/assets?upload=u9");
      fireEvent.click(screen.getByRole("button", { name: /Mezzanine/ }));
      expect(tableNames()).toEqual(["Mezz Pump"]);
      expect(screen.getByTestId("search")).toHaveTextContent("floor=1");
      expect(screen.getByTestId("search")).toHaveTextContent("upload=u9");
      expect(screen.getByRole("button", { name: /Mezzanine/ })).toHaveAttribute("aria-pressed", "true");
    });

    it("restores the filter from the URL", () => {
      renderPage("/facilities/f1/assets?floor=0");
      expect(tableNames()).toEqual(["Ground Press", "Ground Lathe"]);
    });

    it.each(["abc", "7", "", "1.5"])("falls back to all floors for ?floor=%s", (bad) => {
      renderPage(`/facilities/f1/assets?floor=${bad}`);
      expect(tableNames()).toHaveLength(3);
    });

    it("closes the detail drawer when the selected asset's floor is filtered out", () => {
      renderPage("/facilities/f1/assets");
      fireEvent.click(screen.getByRole("button", { name: "Ground Press" }));
      expect(screen.getByRole("complementary", { name: "Details for Ground Press" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Mezzanine/ }));
      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /All floors/ }));
      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    });

    it("shows an empty state with a way back for a floor that has no assets", () => {
      renderPage("/facilities/f1/assets");
      fireEvent.click(screen.getByRole("button", { name: /Roof/ }));
      expect(screen.getByText("No assets on Roof")).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Show all floors" }));
      expect(tableNames()).toHaveLength(3);
      expect(screen.getByTestId("search")).not.toHaveTextContent("floor=");
    });
  });

  it("hides the floor filter for a single-floor facility", () => {
    renderPage();
    expect(screen.queryByRole("group", { name: "Filter by floor" })).not.toBeInTheDocument();
  });

  it("moves focus into the drawer on open and back to the row button on close", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
    const drawer = screen.getByRole("complementary", { name: "Details for Aged Engine" });
    expect(drawer).toHaveFocus();
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("button", { name: "Aged Engine" })).toHaveFocus();
  });

  describe("asset panel", () => {
    const withGroundMap = () =>
      vi.mocked(useFacilities).mockReturnValue(
        query<ReturnType<typeof useFacilities>>({
          data: [
            {
              id: "f1",
              name: "Demo Plant 1",
              latest_map_upload_id: "u0",
              asset_count: 2,
              floors: [{ level: 0, name: "Ground", map_upload_id: "u0", asset_count: 2 }],
            },
          ],
        })
      );

    it("opens beside the table (not below it) with a Show on map link to the asset's floor", () => {
      withGroundMap();
      renderPage("/facilities/f1/assets");
      fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
      const panel = screen.getByRole("complementary", { name: "Details for Aged Engine" });
      expect(panel.parentElement).toHaveClass("results-split");
      expect(panel.previousElementSibling).toContainElement(screen.getByRole("table", { name: "Asset health" }));
      expect(within(panel).getByRole("link", { name: "Show on map" })).toHaveAttribute(
        "href",
        "/facilities/f1/map/u0?asset=a"
      );
    });

    it("hides Show on map when the asset's floor has no finished map", () => {
      renderPage("/facilities/f1/assets");
      fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
      const panel = screen.getByRole("complementary", { name: "Details for Aged Engine" });
      expect(within(panel).queryByRole("link", { name: "Show on map" })).not.toBeInTheDocument();
    });

    it("is read-only on Results: no delete, unlink or state change", () => {
      vi.mocked(useAssetDependencies).mockReturnValue(
        query<ReturnType<typeof useAssetDependencies>>({
          data: [{ id: "d1", facility_id: "f1", parent_asset_id: "a", child_asset_id: "b" }],
        })
      );
      renderPage("/facilities/f1/assets");
      fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
      const panel = screen.getByRole("complementary", { name: "Details for Aged Engine" });
      expect(within(panel).getByRole("button", { name: "New Engine" })).toBeInTheDocument();
      expect(within(panel).queryByRole("button", { name: "Unlink" })).not.toBeInTheDocument();
      expect(within(panel).queryByRole("button", { name: "Delete asset" })).not.toBeInTheDocument();
      expect(within(panel).queryByLabelText("Change operating state")).not.toBeInTheDocument();
    });

    it("jumps to a connected asset on its own floor's map", () => {
      withGroundMap();
      vi.mocked(useAssetDependencies).mockReturnValue(
        query<ReturnType<typeof useAssetDependencies>>({
          data: [{ id: "d1", facility_id: "f1", parent_asset_id: "a", child_asset_id: "b" }],
        })
      );
      renderPage("/facilities/f1/assets");
      fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
      const panel = screen.getByRole("complementary", { name: "Details for Aged Engine" });
      fireEvent.click(within(panel).getByRole("button", { name: "New Engine" }));
      expect(screen.getByTestId("search")).toHaveTextContent("?asset=b");
    });
  });

  it("has no accessibility violations and a valid heading outline, with the panel open", async () => {
    const { container } = renderPage("/facilities/f1/assets?asset=a");
    await screen.findByRole("table", { name: "Asset health" });
    await screen.findByRole("complementary", { name: "Details for Aged Engine" });
    expect(outlineProblems(headingLevels(container))).toEqual([]);
    expect(await a11yViolations(container)).toEqual([]);
  });
});
