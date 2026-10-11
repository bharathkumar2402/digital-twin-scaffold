import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAssetDependencies } from "../hooks/useAssetDependencies";
import { useAssets } from "../hooks/useAssets";
import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { useRiskScores } from "../hooks/useRiskScores";
import type { Asset, RiskScore } from "../types/api";
import { FacilityResultsPage } from "./FacilityResultsPage";

vi.mock("../hooks/useAssets", () => ({ useAssets: vi.fn() }));
vi.mock("../hooks/useAssetDependencies", () => ({ useAssetDependencies: vi.fn() }));
vi.mock("../hooks/useRiskScores", () => ({ useRiskScores: vi.fn() }));
vi.mock("../hooks/useAssetTelemetry", () => ({ useAssetTelemetry: vi.fn() }));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));
// The analysis panel has its own tests; here only whether the page wires it correctly.
vi.mock("../components/AgentAnalysisPanel", () => ({
  AgentAnalysisPanel: (props: { canRun: boolean; assetNames: Map<string, string> }) => (
    <div data-testid="analysis" data-can-run={String(props.canRun)} data-names={props.assetNames.size} />
  ),
}));

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

function renderPage(url = "/facilities/f1/results?upload=u9") {
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/facilities/:facilityId/results" element={<FacilityResultsPage />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>
  );
}

describe("FacilityResultsPage", () => {
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
    vi.mocked(useAssetTelemetry).mockReturnValue(
      query<ReturnType<typeof useAssetTelemetry>>({ data: [] })
    );
    vi.mocked(useAssetDependencies).mockReturnValue(
      query<ReturnType<typeof useAssetDependencies>>({ data: [] })
    );
  });

  it("shows the health table, the analysis panel and the model cards", () => {
    renderPage();
    expect(screen.getByRole("region", { name: "Asset health" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Asset health" })).toBeInTheDocument();
    expect(screen.getByTestId("analysis")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "About these models" })).toBeInTheDocument();
  });

  it("passes the real asset names to the analysis panel", () => {
    renderPage();
    expect(screen.getByTestId("analysis")).toHaveAttribute("data-names", "2");
  });

  it("shows the facility name and Map / Results tabs in the shell, with the map's real upload id", () => {
    renderPage("/facilities/f1/results");
    const nav = screen.getByRole("navigation", { name: "Facility" });
    expect(within(nav).getByText("Demo Plant 1")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Map" })).toHaveAttribute("href", "/facilities/f1/map/u9");
    expect(within(nav).getByRole("link", { name: "Results" })).toHaveClass("nav-active");
  });

  it("disables the Map tab when the facility has no floor plan yet", () => {
    vi.mocked(useFacilities).mockReturnValue(
      query<ReturnType<typeof useFacilities>>({
        data: [{ id: "f1", name: "Demo Plant 1", latest_map_upload_id: null, asset_count: 2, floors: [] }],
      })
    );
    renderPage("/facilities/f1/results");
    const nav = screen.getByRole("navigation", { name: "Facility" });
    expect(within(nav).queryByRole("link", { name: "Map" })).not.toBeInTheDocument();
    expect(within(nav).getByText("Map")).toHaveClass("nav-disabled");
  });

  it("summarises the facility in KPI tiles above the table", () => {
    renderPage();
    const kpis = screen.getByRole("group", { name: "Facility summary" });
    expect(within(kpis).getByText("Assets monitored")).toBeInTheDocument();
    expect(within(kpis).getByText("High risk")).toBeInTheDocument();
    // two assets, one in the high band (95.8), one alarm, shortest RUL 3.6 on the aged engine
    expect(within(kpis).getByText("3.6 cycles")).toBeInTheDocument();
    expect(within(kpis).getByText("Aged Engine")).toBeInTheDocument();
  });

  it.each([
    ["superadmin", true],
    ["tenant_admin", true],
    ["facility_manager", true],
    ["technician", false],
    ["viewer", false],
  ])("role %s may run analyses: %s", (role, allowed) => {
    vi.mocked(useAuth).mockReturnValue({ role } as ReturnType<typeof useAuth>);
    renderPage();
    expect(screen.getByTestId("analysis")).toHaveAttribute("data-can-run", String(allowed));
  });

  it("does not offer running when the role is unknown", () => {
    vi.mocked(useAuth).mockReturnValue({ role: null } as ReturnType<typeof useAuth>);
    renderPage();
    expect(screen.getByTestId("analysis")).toHaveAttribute("data-can-run", "false");
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

  it("shows a skeleton (busy, no table) while the data loads", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ isPending: true }));
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ isPending: true }));
    renderPage();
    expect(screen.getByRole("status", { name: "Loading results" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Facility summary" })).not.toBeInTheDocument();
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
      renderPage("/facilities/f1/results");
      const group = screen.getByRole("group", { name: "Filter by floor" });
      expect(within(group).getByRole("button", { name: /All floors/ })).toHaveAttribute("aria-pressed", "true");
      expect(within(group).getByRole("button", { name: /Ground.*2 assets.*1 high risk/ })).toBeInTheDocument();
      expect(within(group).getByRole("button", { name: /Mezzanine.*1 asset\b.*0 high risk/ })).toBeInTheDocument();
      expect(tableNames()).toHaveLength(3);
    });

    it("filters the table and KPI tiles to the chosen floor and writes ?floor= to the URL", () => {
      renderPage("/facilities/f1/results?upload=u9");
      fireEvent.click(screen.getByRole("button", { name: /Mezzanine/ }));
      expect(tableNames()).toEqual(["Mezz Pump"]);
      expect(screen.getByTestId("search")).toHaveTextContent("floor=1");
      expect(screen.getByTestId("search")).toHaveTextContent("upload=u9");
      const kpis = screen.getByRole("group", { name: "Summary for Mezzanine" });
      expect(within(kpis).getByText("Mezz Pump")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Mezzanine/ })).toHaveAttribute("aria-pressed", "true");
    });

    it("restores the filter from the URL", () => {
      renderPage("/facilities/f1/results?floor=0");
      expect(tableNames()).toEqual(["Ground Press", "Ground Lathe"]);
    });

    it.each(["abc", "7", "", "1.5"])("falls back to all floors for ?floor=%s", (bad) => {
      renderPage(`/facilities/f1/results?floor=${bad}`);
      expect(tableNames()).toHaveLength(3);
      expect(screen.getByRole("group", { name: "Facility summary" })).toBeInTheDocument();
    });

    it("closes the detail drawer when the selected asset's floor is filtered out", () => {
      renderPage("/facilities/f1/results");
      fireEvent.click(screen.getByRole("button", { name: "Ground Press" }));
      expect(screen.getByRole("complementary", { name: "Details for Ground Press" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Mezzanine/ }));
      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /All floors/ }));
      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    });

    it("shows an empty state with a way back for a floor that has no assets", () => {
      renderPage("/facilities/f1/results");
      fireEvent.click(screen.getByRole("button", { name: /Roof/ }));
      expect(screen.getByText("No assets on Roof")).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Show all floors" }));
      expect(tableNames()).toHaveLength(3);
      expect(screen.getByTestId("search")).not.toHaveTextContent("floor=");
    });

    it("labels agent analysis as facility-wide", () => {
      renderPage("/facilities/f1/results?floor=1");
      expect(screen.getByText(/whole facility, whichever floor/)).toBeInTheDocument();
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
      renderPage("/facilities/f1/results");
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
      renderPage("/facilities/f1/results");
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
      renderPage("/facilities/f1/results");
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
      renderPage("/facilities/f1/results");
      fireEvent.click(screen.getByRole("button", { name: "Aged Engine" }));
      const panel = screen.getByRole("complementary", { name: "Details for Aged Engine" });
      fireEvent.click(within(panel).getByRole("button", { name: "New Engine" }));
      expect(screen.getByTestId("search")).toHaveTextContent("?asset=b");
    });
  });
});
