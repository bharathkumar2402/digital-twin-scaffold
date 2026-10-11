import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentRun, useAgentRuns } from "../hooks/useAgentRuns";
import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import type { AgentRunDetail, AgentRunState, Asset } from "../types/api";
import { FacilityWorkPage } from "./FacilityWorkPage";

vi.mock("../hooks/useAgentRuns", () => ({ useAgentRuns: vi.fn(), useAgentRun: vi.fn() }));
vi.mock("../hooks/useAssets", () => ({ useAssets: vi.fn() }));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));

const asset = (id: string, name: string): Asset => ({
  id,
  facility_id: "f1",
  name,
  type: "pump",
  floor_level: 0,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
});

const summary = (id: string, status: string, createdAt: string) => ({
  id,
  tenant_id: "t",
  facility_id: "f1",
  trigger: "user_query",
  status,
  duration_ms: 1,
  validation_errors_json: [],
  decision_report: null,
  confidence: null,
  created_at: createdAt,
});

function detail(id: string, createdAt: string, state: Partial<AgentRunState>): AgentRunDetail {
  return {
    ...summary(id, "completed", createdAt),
    state_snapshot_json: {
      risk_scores: null,
      maintenance_schedule: null,
      inventory_gaps: null,
      dispatch_routes: null,
      simulation_result: null,
      ...state,
    },
  };
}

const recent = () => new Date(Date.now() - 2 * 3_600_000).toISOString();
const old = () => new Date(Date.now() - 30 * 3_600_000).toISOString();

const goodState: Partial<AgentRunState> = {
  maintenance_schedule: [
    {
      item_id: "t1",
      asset_id: "a1",
      scheduled_date: "2026-10-12",
      priority: "critical",
      estimated_duration_hours: 2,
      required_technician_skills: [],
      required_parts: [],
    },
    {
      item_id: "t2",
      asset_id: "a2",
      scheduled_date: "2026-10-13",
      priority: "low",
      estimated_duration_hours: 1,
      required_technician_skills: [],
      required_parts: [],
    },
  ],
  dispatch_routes: [
    {
      technician_id: "tech-1",
      technician_name: "Alex",
      assigned_stops: [
        {
          stop_number: 1,
          asset_id: "a1",
          task_id: "t1",
          estimated_arrival_minutes: 10,
          service_duration_minutes: 45,
          travel_time_from_previous_minutes: 10,
        },
      ],
      total_travel_minutes: 10,
      total_service_minutes: 45,
      total_route_duration_minutes: 55,
    },
  ],
};

function q<T>(over: Record<string, unknown>) {
  return { data: undefined, isPending: false, isError: false, refetch: vi.fn(), ...over } as T;
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/facilities/f1/work"]}>
      <Routes>
        <Route path="/facilities/:facilityId/work" element={<FacilityWorkPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("FacilityWorkPage", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ role: "technician", logout: vi.fn() } as unknown as ReturnType<
      typeof useAuth
    >);
    vi.mocked(useFacilities).mockReturnValue(
      q<ReturnType<typeof useFacilities>>({
        data: [
          {
            id: "f1",
            name: "Meridian",
            latest_map_upload_id: "u0",
            asset_count: 2,
            floors: [{ level: 0, name: "Ground", map_upload_id: "u0", asset_count: 2 }],
          },
        ],
      })
    );
    vi.mocked(useAssets).mockReturnValue(
      q<ReturnType<typeof useAssets>>({ data: [asset("a1", "Feed Pump"), asset("a2", "Press B")] })
    );
    vi.mocked(useAgentRuns).mockReturnValue(
      q<ReturnType<typeof useAgentRuns>>({
        data: [
          summary("failed-newest", "failed", recent()),
          summary("good", "completed", recent()),
        ],
      })
    );
    vi.mocked(useAgentRun).mockReturnValue(
      q<ReturnType<typeof useAgentRun>>({ data: detail("good", recent(), goodState) })
    );
  });

  it("asks for the newest completed run's detail, skipping a newer failed one", () => {
    renderPage();
    expect(vi.mocked(useAgentRun)).toHaveBeenCalledWith("good");
  });

  it("shows the technician's route as ordered stops, with unassigned jobs called out", () => {
    renderPage();
    const alex = screen.getByRole("region", { name: "Alex" });
    expect(alex).toHaveTextContent("1 stop");
    expect(within(alex).getByRole("listitem", { name: "Stop 1: Feed Pump" })).toBeInTheDocument();
    // t2 is scheduled a day after t1, so it is "later", not "unassigned"
    expect(screen.queryByRole("region", { name: "Not assigned" })).not.toBeInTheDocument();
    const later = screen.getByRole("region", { name: "Scheduled for later" });
    expect(later).toHaveTextContent("1 more job on later days");
    expect(later).toHaveTextContent("Press B (low)");
    const shift = screen.getByText(/for the shift of/);
    expect(shift).toHaveTextContent(/12/);
    expect(shift).toHaveTextContent(/Oct/);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("calls out a job that was due this shift but not routed", () => {
    const withDueLeftover: Partial<AgentRunState> = {
      ...goodState,
      maintenance_schedule: [
        ...(goodState.maintenance_schedule ?? []),
        {
          item_id: "t3",
          asset_id: "a2",
          scheduled_date: "2026-10-12",
          priority: "high",
          estimated_duration_hours: 3,
          required_technician_skills: [],
          required_parts: [],
        },
      ],
    };
    vi.mocked(useAgentRun).mockReturnValue(
      q<ReturnType<typeof useAgentRun>>({ data: detail("good", recent(), withDueLeftover) })
    );
    renderPage();
    const unassigned = screen.getByRole("region", { name: "Not assigned" });
    expect(unassigned).toHaveTextContent("Press B (high priority)");
    expect(unassigned).toHaveTextContent("due for this shift");
  });

  it("lists later jobs grouped by day in a section that starts collapsed", () => {
    renderPage();
    const details = screen.getByRole("region", { name: "Scheduled for later" }).querySelector("details");
    expect(details).not.toBeNull();
    expect((details as HTMLDetailsElement).open).toBe(false);
    // day formats follow the viewer's locale ("Oct 13" or "13 Oct"), so check the parts
    expect(details).toHaveTextContent(/(13 Oct|Oct 13): Press B \(low\)/);
  });

  it("warns when the plan is more than a day old", () => {
    vi.mocked(useAgentRun).mockReturnValue(
      q<ReturnType<typeof useAgentRun>>({ data: detail("good", old(), goodState) })
    );
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("more than a day old");
  });

  it("is read-only: no run, edit or delete controls", () => {
    renderPage();
    expect(screen.queryByRole("button", { name: /run|delete|unlink/i })).not.toBeInTheDocument();
  });

  it("shows a skeleton while loading", () => {
    vi.mocked(useAgentRuns).mockReturnValue(q<ReturnType<typeof useAgentRuns>>({ isPending: true }));
    vi.mocked(useAgentRun).mockReturnValue(q<ReturnType<typeof useAgentRun>>({ isPending: true }));
    renderPage();
    expect(screen.getByRole("status", { name: "Loading results" })).toBeInTheDocument();
  });

  it("shows an error with Retry that refetches", () => {
    const refetch = vi.fn();
    vi.mocked(useAgentRuns).mockReturnValue(q<ReturnType<typeof useAgentRuns>>({ isError: true, refetch }));
    vi.mocked(useAgentRun).mockReturnValue(q<ReturnType<typeof useAgentRun>>({}));
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load the work plan");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("tells a technician to ask a manager when no analysis has completed", () => {
    vi.mocked(useAgentRuns).mockReturnValue(
      q<ReturnType<typeof useAgentRuns>>({ data: [summary("r", "failed", recent())] })
    );
    vi.mocked(useAgentRun).mockReturnValue(q<ReturnType<typeof useAgentRun>>({}));
    renderPage();
    expect(screen.getByText("No work plan yet")).toBeInTheDocument();
    expect(screen.getByText(/A manager needs to run an analysis/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Run an analysis" })).not.toBeInTheDocument();
  });

  it("offers a manager a link to run one", () => {
    vi.mocked(useAuth).mockReturnValue({ role: "facility_manager", logout: vi.fn() } as unknown as ReturnType<
      typeof useAuth
    >);
    vi.mocked(useAgentRuns).mockReturnValue(q<ReturnType<typeof useAgentRuns>>({ data: [] }));
    vi.mocked(useAgentRun).mockReturnValue(q<ReturnType<typeof useAgentRun>>({}));
    renderPage();
    expect(screen.getByRole("link", { name: "Run an analysis" })).toHaveAttribute(
      "href",
      "/facilities/f1/analysis"
    );
  });

  it("says nothing to do when the analysis scheduled no maintenance", () => {
    vi.mocked(useAgentRun).mockReturnValue(
      q<ReturnType<typeof useAgentRun>>({ data: detail("good", recent(), {}) })
    );
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Nothing to do");
  });
});
