import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { useRiskScores } from "../hooks/useRiskScores";
import { a11yViolations, headingLevels, outlineProblems } from "../test/a11y";
import { FacilityAnalysisPage } from "./FacilityAnalysisPage";

vi.mock("../hooks/useAssets", () => ({ useAssets: vi.fn() }));
vi.mock("../hooks/useTelemetryFreshness", () => ({
  useTelemetryFreshness: vi.fn(() => ({ data: undefined })),
}));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));
vi.mock("../hooks/useRiskScores", () => ({ useRiskScores: vi.fn() }));
// The analysis panel has its own tests; here only whether the page wires it correctly.
vi.mock("../components/AgentAnalysisPanel", () => ({
  AgentAnalysisPanel: (props: { canRun: boolean; assetNames: Map<string, string>; suggestions?: string[] }) => (
    <div
      data-testid="analysis"
      data-can-run={String(props.canRun)}
      data-names={props.assetNames.size}
      data-suggestions={JSON.stringify(props.suggestions ?? [])}
    />
  ),
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/facilities/f1/analysis"]}>
      <Routes>
        <Route path="/facilities/:facilityId/analysis" element={<FacilityAnalysisPage />} />
      </Routes>
    </MemoryRouter>
  );
}

const asset = (id: string, name: string) => ({
  id,
  facility_id: "f1",
  name,
  type: "pump",
  floor_level: 0,
  x: 0,
  y: 0,
  status: "operational" as const,
  installed_date: null,
  manufacturer: null,
  model: null,
});

describe("FacilityAnalysisPage", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ role: "facility_manager", logout: vi.fn() } as unknown as ReturnType<
      typeof useAuth
    >);
    vi.mocked(useFacilities).mockReturnValue({
      data: [{ id: "f1", name: "Meridian", latest_map_upload_id: null, asset_count: 2, floors: [] }],
    } as unknown as ReturnType<typeof useFacilities>);
    vi.mocked(useAssets).mockReturnValue({
      data: [asset("a", "Aged Engine"), asset("b", "New Engine")],
    } as unknown as ReturnType<typeof useAssets>);
    vi.mocked(useRiskScores).mockReturnValue({
      data: [
        {
          id: "r1",
          facility_id: "f1",
          asset_id: "a",
          score: 95.8,
          rul_cycles: 3.6,
          model_version: "v1",
          factors_json: {},
          computed_at: "2026-10-10T10:00:00Z",
        },
      ],
    } as unknown as ReturnType<typeof useRiskScores>);
  });

  it("suggests what-if questions about the machines that need attention", () => {
    renderPage();
    expect(JSON.parse(screen.getByTestId("analysis").getAttribute("data-suggestions") ?? "[]")).toEqual([
      "What if Aged Engine fails?",
    ]);
  });

  it("shows only the analysis panel, labelled as facility-wide", () => {
    renderPage();
    expect(screen.getByRole("region", { name: "Agent analysis" })).toBeInTheDocument();
    expect(screen.getByText(/whole facility, not a single floor/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("passes the real asset names to the panel", () => {
    renderPage();
    expect(screen.getByTestId("analysis")).toHaveAttribute("data-names", "2");
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

  it("has no accessibility violations and a valid heading outline", async () => {
    const { container } = renderPage();
    await screen.findByRole("region", { name: "Agent analysis" });
    expect(outlineProblems(headingLevels(container))).toEqual([]);
    expect(await a11yViolations(container)).toEqual([]);
  });
});
