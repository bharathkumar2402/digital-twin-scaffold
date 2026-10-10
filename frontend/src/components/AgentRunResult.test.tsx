import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AgentRunDetail, AgentRunState } from "../types/api";
import { AgentRunResult } from "./AgentRunResult";

const PUMP = "11111111-1111-1111-1111-111111111111";
const HX = "22222222-2222-2222-2222-222222222222";
const GHOST = "99999999-9999-9999-9999-999999999999";
const names = new Map([
  [PUMP, "Primary Feed Pump 7"],
  [HX, "Heat Exchanger Alpha"],
]);

const fullState: AgentRunState = {
  risk_scores: [
    {
      asset_id: HX,
      risk_score: 20,
      rul_cycles: null,
      rul_model_version: null,
      urgency_rank: 2,
      risk_tier: "low",
      predicted_failure_mode: null,
      primary_risk_factors: ["Stable telemetry"],
      recommended_action: "Routine inspection",
    },
    {
      asset_id: PUMP,
      risk_score: 98.7,
      rul_cycles: 3.6,
      rul_model_version: "v1",
      urgency_rank: 1,
      risk_tier: "critical",
      predicted_failure_mode: "OSF",
      primary_risk_factors: ["Overstrain", "Estimated remaining useful life: 3.6 cycles"],
      recommended_action: "Immediate intervention",
    },
  ],
  maintenance_schedule: [
    {
      item_id: "m1",
      asset_id: PUMP,
      scheduled_date: "2026-10-10",
      priority: "critical",
      estimated_duration_hours: 3.5,
      required_technician_skills: ["mechanical"],
      required_parts: [{ part_id: "SKU-1", part_name: "Seal kit", quantity: 2 }],
    },
  ],
  inventory_gaps: [
    {
      part_id: "SKU-1",
      part_name: "Seal kit",
      needed_quantity: 2,
      available_quantity: 0,
      shortage_count: 2,
      lead_time_days: 5,
      impacted_asset_ids: [PUMP],
    },
  ],
  dispatch_routes: [
    {
      technician_id: "t1",
      technician_name: "Jordan Lee",
      total_travel_minutes: 10,
      total_service_minutes: 210,
      total_route_duration_minutes: 220,
      assigned_stops: [
        {
          stop_number: 1,
          asset_id: PUMP,
          task_id: "m1",
          estimated_arrival_minutes: 10,
          service_duration_minutes: 210,
          travel_time_from_previous_minutes: 10,
        },
      ],
    },
  ],
  simulation_result: {
    root_cause_asset_id: PUMP,
    directly_affected_asset_ids: [HX],
    downstream_shutoff_asset_ids: [HX],
    total_affected_assets: 2,
    critical_subsystems_interrupted: ["Heat Exchanger Alpha (heat_exchanger)"],
    estimated_downtime_hours: 6.5,
    cascade_depth: 2,
  },
  user_query: "What if Primary Feed Pump 7 fails?",
};

function run(overrides: Partial<AgentRunDetail> = {}, state: Partial<AgentRunState> = {}): AgentRunDetail {
  return {
    id: "run-1",
    tenant_id: "t",
    facility_id: "f",
    trigger: "user_query",
    status: "completed",
    duration_ms: 9300,
    validation_errors_json: [],
    decision_report: "Failure at the pump threatens 2 assets.",
    confidence: 0.89,
    created_at: "2026-10-10T10:00:00Z",
    state_snapshot_json: { ...fullState, ...state },
    ...overrides,
  };
}

describe("AgentRunResult", () => {
  it("shows asset names (never raw ids) in every stage", () => {
    const { container } = render(<AgentRunResult run={run()} assetNames={names} />);
    expect(screen.getAllByText("Primary Feed Pump 7").length).toBeGreaterThan(2);
    expect(container.textContent).not.toContain(PUMP);
    expect(container.textContent).not.toContain(HX);
  });

  it("orders risk priorities by urgency rank", () => {
    render(<AgentRunResult run={run()} assetNames={names} />);
    const card = screen.getByRole("region", { name: "1. Risk priorities" });
    const rows = within(card).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Primary Feed Pump 7")).toBeInTheDocument();
    expect(within(rows[0]).getByText("critical")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Heat Exchanger Alpha")).toBeInTheDocument();
  });

  it("renders maintenance, parts, shortages, routes, cascade and the summary", () => {
    render(<AgentRunResult run={run()} assetNames={names} />);
    expect(screen.getByText(/2× Seal kit/)).toBeInTheDocument();
    expect(screen.getByText(/need 2, have 0/)).toBeInTheDocument();
    expect(screen.getByText(/Jordan Lee/)).toBeInTheDocument();
    expect(screen.getByText(/2 asset\(s\) are affected/)).toBeInTheDocument();
    expect(screen.getByText(/not a prediction/)).toBeInTheDocument();
    expect(screen.getByText("Failure at the pump threatens 2 assets.")).toBeInTheDocument();
  });

  it("always renders the validation errors section, even when there are none", () => {
    render(<AgentRunResult run={run()} assetNames={names} />);
    const card = screen.getByRole("region", { name: "Validation errors" });
    expect(within(card).getByText(/None/)).toBeInTheDocument();
  });

  it("lists validation errors when present", () => {
    render(
      <AgentRunResult
        run={run({ validation_errors_json: ["risk_assessment: score 140 > 100"] })}
        assetNames={names}
      />
    );
    const card = screen.getByRole("region", { name: "Validation errors" });
    expect(within(card).getByText("risk_assessment: score 140 > 100")).toBeInTheDocument();
  });

  it("shows confidence for a completed run only", () => {
    const { rerender } = render(<AgentRunResult run={run()} assetNames={names} />);
    expect(screen.getByText("confidence 89%")).toBeInTheDocument();
    rerender(
      <AgentRunResult
        run={run({ status: "halted_for_escalation", confidence: 0.95 })}
        assetNames={names}
      />
    );
    expect(screen.queryByText(/confidence/)).not.toBeInTheDocument();
  });

  it("shows a halted run's reason prominently and still shows completed stages", () => {
    render(
      <AgentRunResult
        run={run(
          { status: "halted_for_escalation", decision_report: null },
          {
            simulation_result: null,
            escalation_reason:
              "asset 'Flux Capacitor' was not found in this facility (known assets: Pump)",
            errors: ["Simulation decision node runtime error: boom"],
          }
        )}
        assetNames={names}
      />
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The pipeline stopped before finishing");
    expect(alert).toHaveTextContent("Flux Capacitor");
    expect(alert).toHaveTextContent("Simulation decision node runtime error: boom");
    expect(screen.getAllByText("Primary Feed Pump 7").length).toBeGreaterThan(0); // earlier stages
    expect(screen.getByRole("region", { name: "4. Failure cascade (what-if)" })).toHaveTextContent(
      "Not produced"
    );
    expect(screen.getByText("No summary was produced.")).toBeInTheDocument();
  });

  it("says so when a halted run has no recorded reason", () => {
    render(
      <AgentRunResult
        run={run({ status: "failed" }, { escalation_reason: null })}
        assetNames={names}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No reason was recorded");
  });

  it("flags an asset id that is not in the facility instead of crashing or hiding it", () => {
    render(
      <AgentRunResult
        run={run({}, { risk_scores: [{ ...fullState.risk_scores![0], asset_id: GHOST }] })}
        assetNames={names}
      />
    );
    expect(screen.getByText(/Unknown asset \(99999999/)).toBeInTheDocument();
  });

  it("shows asset names, not ids, inside the narrative summary", () => {
    const { container } = render(
      <AgentRunResult
        run={run({ decision_report: `Failure at root asset ${PUMP} threatens 2 asset(s).` })}
        assetNames={names}
      />
    );
    expect(screen.getByText("Failure at root asset Primary Feed Pump 7 threatens 2 asset(s).")).toBeInTheDocument();
    expect(container.textContent).not.toContain(PUMP);
  });

  it("also names assets inside a halted run's reason and errors", () => {
    render(
      <AgentRunResult
        run={run(
          { status: "halted_for_escalation" },
          { escalation_reason: `failed at ${PUMP}`, errors: [`node error for ${HX}`] }
        )}
        assetNames={names}
      />
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("failed at Primary Feed Pump 7");
    expect(alert).toHaveTextContent("node error for Heat Exchanger Alpha");
  });

  it("labels the 'why' text as rule-based, not the model's reasoning", () => {
    render(<AgentRunResult run={run()} assetNames={names} />);
    expect(screen.getByText(/rule-based explanation over recent telemetry/)).toBeInTheDocument();
  });

  it("tolerates stages that are entirely missing", () => {
    render(
      <AgentRunResult
        run={run(
          { decision_report: null },
          {
            risk_scores: null,
            maintenance_schedule: null,
            inventory_gaps: null,
            dispatch_routes: null,
            simulation_result: null,
          }
        )}
        assetNames={names}
      />
    );
    expect(screen.getAllByText(/Not produced/).length).toBe(4);
  });
});
