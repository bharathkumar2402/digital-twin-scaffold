import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { useRiskHistory } from "../hooks/useRiskHistory";
import { useRiskScores } from "../hooks/useRiskScores";
import { useTelemetryFreshness } from "../hooks/useTelemetryFreshness";
import type { Asset, RiskScore } from "../types/api";
import { a11yViolations, headingLevels, outlineProblems } from "../test/a11y";
import { FacilityOverviewPage } from "./FacilityOverviewPage";

vi.mock("../hooks/useAssets", () => ({ useAssets: vi.fn() }));
vi.mock("../hooks/useTelemetryFreshness", () => ({
  useTelemetryFreshness: vi.fn(() => ({ data: undefined })),
}));
vi.mock("../hooks/useRiskScores", () => ({ useRiskScores: vi.fn() }));
vi.mock("../hooks/useRiskHistory", () => ({ useRiskHistory: vi.fn() }));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));

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

const score = (assetId: string, value: number, rul: number | null): RiskScore => ({
  id: `r-${assetId}`,
  facility_id: "f1",
  asset_id: assetId,
  score: value,
  rul_cycles: rul,
  model_version: "v1",
  factors_json: {},
  computed_at: "2026-10-10T10:00:00Z",
});

function query<T>(over: Record<string, unknown>) {
  return { data: undefined, isPending: false, isError: false, ...over } as T;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/facilities/f1/overview"]}>
      <Routes>
        <Route path="/facilities/:facilityId/overview" element={<FacilityOverviewPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("FacilityOverviewPage", () => {
  beforeEach(() => {
    vi.mocked(useTelemetryFreshness).mockReturnValue({ data: undefined } as unknown as ReturnType<
      typeof useTelemetryFreshness
    >);
    vi.mocked(useAuth).mockReturnValue({ role: "facility_manager", logout: vi.fn() } as unknown as ReturnType<
      typeof useAuth
    >);
    vi.mocked(useFacilities).mockReturnValue(
      query<ReturnType<typeof useFacilities>>({
        data: [
          {
            id: "f1",
            name: "Meridian",
            latest_map_upload_id: "u0",
            asset_count: 3,
            floors: [
              { level: 0, name: "Ground", map_upload_id: "u0", asset_count: 2 },
              { level: 1, name: "Mezzanine", map_upload_id: "u1", asset_count: 1 },
            ],
          },
        ],
      })
    );
    vi.mocked(useRiskHistory).mockReturnValue({ byAsset: new Map(), isError: false });
    vi.mocked(useAssets).mockReturnValue(
      query<ReturnType<typeof useAssets>>({
        data: [asset("a", "Aged Engine", 0), asset("b", "New Engine", 0), asset("c", "Mezz Pump", 1)],
      })
    );
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({
        data: [score("a", 95.8, 3.6), score("b", 0.4, 115.9), score("c", 50, 40)],
      })
    );
  });

  it("summarises the whole facility in KPI tiles, with no table (that is the Assets tab)", () => {
    renderPage();
    const kpis = screen.getByRole("group", { name: "Facility summary" });
    expect(within(kpis).getByText("3")).toBeInTheDocument();
    expect(within(kpis).getByText("3.6 cycles")).toBeInTheDocument();
    expect(within(kpis).getByText("Aged Engine")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Agent analysis" })).not.toBeInTheDocument();
  });

  it("shows a healthy-scored machine that stopped reporting as No recent data, never as healthy", () => {
    vi.mocked(useTelemetryFreshness).mockReturnValue({
      data: {
        as_of: "2026-10-12T12:00:00Z",
        assets: [
          { asset_id: "a", last_reading_at: "2026-10-12T11:59:50Z" },
          { asset_id: "b", last_reading_at: "2026-10-12T11:30:00Z" }, // New Engine, risk 0.4
          { asset_id: "c", last_reading_at: "2026-10-12T11:59:50Z" },
        ],
      },
    } as unknown as ReturnType<typeof useTelemetryFreshness>);
    renderPage();
    const section = screen.getByRole("region", { name: "Needs attention" });
    const quiet = within(section).getByRole("listitem", { name: "New Engine" });
    expect(quiet).toHaveTextContent("No recent data");
    expect(quiet).not.toHaveTextContent("Healthy");
    expect(quiet).toHaveTextContent(/no sensor reading for 30 min ago/);
    expect(screen.getByRole("group", { name: "Facility summary" })).toHaveTextContent("1 no recent data");
  });

  it("lists the machines needing attention, worst first, and leaves out healthy ones", () => {
    renderPage();
    const section = screen.getByRole("region", { name: "Needs attention" });
    const cards = within(section).getAllByRole("listitem");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Aged Engine", "Mezz Pump"]);
    expect(within(section).queryByText("New Engine")).not.toBeInTheDocument();
    expect(within(cards[0]).getByRole("link", { name: "Details" })).toHaveAttribute(
      "href",
      "/facilities/f1/assets?asset=a"
    );
    // Mezzanine has a map in the fixture; its card links there, and Ground's too.
    expect(within(cards[1]).getByRole("link", { name: "Show on map" })).toHaveAttribute(
      "href",
      "/facilities/f1/map/u1?asset=c"
    );
  });

  it("shows how a machine's risk moved, from the history route", () => {
    vi.mocked(useRiskHistory).mockReturnValue({
      byAsset: new Map([
        [
          "a",
          [
            { computed_at: "2026-10-01T08:00:00Z", score: 70, rul_cycles: null },
            { computed_at: "2026-10-01T11:00:00Z", score: 95.8, rul_cycles: 3.6 },
          ],
        ],
      ]),
      isError: false,
    });
    renderPage();
    const aged = screen.getByRole("listitem", { name: "Aged Engine" });
    expect(aged).toHaveTextContent("▲ up 25.8 pts in 3 h");
  });

  it("still shows every card when the history request failed", () => {
    vi.mocked(useRiskHistory).mockReturnValue({ byAsset: new Map(), isError: true });
    renderPage();
    expect(screen.getByRole("listitem", { name: "Aged Engine" })).toBeInTheDocument();
    expect(screen.queryByText(/pts in/)).not.toBeInTheDocument();
  });

  it("shows an all-clear instead of an empty list when everything is healthy", () => {
    vi.mocked(useRiskScores).mockReturnValue(
      query<ReturnType<typeof useRiskScores>>({
        data: [score("a", 3, 100), score("b", 0.4, 115.9), score("c", 5, 90)],
      })
    );
    renderPage();
    const section = screen.getByRole("region", { name: "Needs attention" });
    expect(within(section).getByRole("status")).toHaveTextContent("All clear");
    expect(within(section).queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("links each floor card to the Assets tab filtered to that floor, with counts", () => {
    renderPage();
    const floors = screen.getByRole("navigation", { name: "Floors" });
    const ground = within(floors).getByRole("link", { name: /Ground/ });
    expect(ground).toHaveAttribute("href", "/facilities/f1/assets?floor=0");
    expect(ground).toHaveTextContent("2 assets");
    expect(ground).toHaveTextContent("1 high risk");
    expect(within(floors).getByRole("link", { name: /Mezzanine/ })).toHaveAttribute(
      "href",
      "/facilities/f1/assets?floor=1"
    );
    expect(within(floors).getByRole("link", { name: /All floors/ })).toHaveAttribute(
      "href",
      "/facilities/f1/assets"
    );
  });

  it("shows a skeleton while loading", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ isPending: true }));
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ isPending: true }));
    renderPage();
    expect(screen.getByRole("status", { name: "Loading results" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("group", { name: "Facility summary" })).not.toBeInTheDocument();
  });

  it("names what failed and retries only that query", () => {
    const refetch = vi.fn();
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ isError: true, refetch }));
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load this facility’s assets");
    screen.getByRole("button", { name: "Retry" }).click();
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("explains an empty facility and a facility with no scores yet", () => {
    vi.mocked(useAssets).mockReturnValue(query<ReturnType<typeof useAssets>>({ data: [] }));
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ data: [] }));
    renderPage();
    expect(screen.getByText("No assets in this facility yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the map" })).toHaveAttribute("href", "/facilities/f1/map/u0");
  });

  it("says scoring has not run when there are assets but no scores", () => {
    vi.mocked(useRiskScores).mockReturnValue(query<ReturnType<typeof useRiskScores>>({ data: [] }));
    renderPage();
    expect(screen.getByText("No risk scores yet")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Facility summary" })).not.toBeInTheDocument();
  });

  it("has no accessibility violations and a valid heading outline", async () => {
    const { container } = renderPage();
    await screen.findByRole("region", { name: "Needs attention" });
    expect(outlineProblems(headingLevels(container))).toEqual([]);
    expect(await a11yViolations(container)).toEqual([]);
  });
});
