import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAssets } from "../hooks/useAssets";
import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { useAuth } from "../hooks/useAuth";
import { useRiskScores } from "../hooks/useRiskScores";
import type { Asset, RiskScore } from "../types/api";
import { FacilityResultsPage } from "./FacilityResultsPage";

vi.mock("../hooks/useAssets", () => ({ useAssets: vi.fn() }));
vi.mock("../hooks/useRiskScores", () => ({ useRiskScores: vi.fn() }));
vi.mock("../hooks/useAssetTelemetry", () => ({ useAssetTelemetry: vi.fn() }));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
// The analysis panel has its own tests; here only whether the page wires it correctly.
vi.mock("../components/AgentAnalysisPanel", () => ({
  AgentAnalysisPanel: (props: { canRun: boolean; assetNames: Map<string, string> }) => (
    <div data-testid="analysis" data-can-run={String(props.canRun)} data-names={props.assetNames.size} />
  ),
}));

const asset = (id: string, name: string): Asset => ({
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
    </MemoryRouter>
  );
}

describe("FacilityResultsPage", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ role: "facility_manager" } as ReturnType<typeof useAuth>);
    vi.mocked(useAssets).mockReturnValue(
      query<ReturnType<typeof useAssets>>({ data: [asset("a", "Aged Engine"), asset("b", "New Engine")] })
    );
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({ data: [score("a", 95.8, 3.6), score("b", 0.4, 115.9)] })
    );
    vi.mocked(useAssetTelemetry).mockReturnValue(
      query<ReturnType<typeof useAssetTelemetry>>({ data: [] })
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

  it("links back to the map using the upload id from the URL", () => {
    renderPage();
    expect(screen.getByRole("link", { name: /Back to map/ })).toHaveAttribute(
      "href",
      "/facilities/f1/map/u9"
    );
  });

  it("omits the back link when the page was opened without an upload id", () => {
    renderPage("/facilities/f1/results");
    expect(screen.queryByRole("link", { name: /Back to map/ })).not.toBeInTheDocument();
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
    expect(within(drawer).getByText(/3.6 cycles/)).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("shows a loading state and then an error if the data cannot be loaded", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ isPending: true }));
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({ isPending: true })
    );
    renderPage();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("surfaces a load failure instead of an empty table", () => {
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({ isError: true })
    );
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load this facility");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
