import { describe, expect, it } from "vitest";

import type { AgentRunState, AgentRunSummary, Asset } from "../types/api";
import { buildWorkPlan, formatMinutes, latestCompletedRun, planAge } from "./work";

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

const assetsById = new Map([asset("a1", "Pump A"), asset("a2", "Press B")].map((a) => [a.id, a]));

const run = (id: string, status: string, createdAt: string): AgentRunSummary => ({
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

function state(over: Partial<AgentRunState>): AgentRunState {
  return {
    risk_scores: null,
    maintenance_schedule: null,
    inventory_gaps: null,
    dispatch_routes: null,
    simulation_result: null,
    ...over,
  };
}

const task = (id: string, assetId: string, parts: { part_id: string; part_name: string; quantity: number }[] = []) => ({
  item_id: id,
  asset_id: assetId,
  scheduled_date: "2026-10-12",
  priority: "high" as const,
  estimated_duration_hours: 2,
  required_technician_skills: [],
  required_parts: parts,
});

const stop = (n: number, assetId: string, taskId: string, arrival = 30) => ({
  stop_number: n,
  asset_id: assetId,
  task_id: taskId,
  estimated_arrival_minutes: arrival,
  service_duration_minutes: 45,
  travel_time_from_previous_minutes: 5,
});

const route = (stops: ReturnType<typeof stop>[]) => ({
  technician_id: "tech-1",
  technician_name: "Alex",
  assigned_stops: stops,
  total_travel_minutes: 10,
  total_service_minutes: 90,
  total_route_duration_minutes: 100,
});

describe("latestCompletedRun", () => {
  it("returns the newest completed run, skipping failed and halted ones", () => {
    const runs = [
      run("r3", "failed", "2026-10-11T12:00:00Z"),
      run("r2", "halted_for_escalation", "2026-10-11T11:00:00Z"),
      run("r1", "completed", "2026-10-11T10:00:00Z"),
      run("r0", "completed", "2026-10-10T10:00:00Z"),
    ];
    expect(latestCompletedRun(runs)?.id).toBe("r1");
  });

  it("returns null when nothing completed", () => {
    expect(latestCompletedRun([run("r", "failed", "2026-10-11T10:00:00Z")])).toBeNull();
    expect(latestCompletedRun([])).toBeNull();
  });
});

describe("buildWorkPlan", () => {
  it("orders stops by stop number, not by array order, and joins asset and task details", () => {
    const plan = buildWorkPlan(
      state({
        maintenance_schedule: [task("t1", "a1"), task("t2", "a2")],
        dispatch_routes: [route([stop(2, "a2", "t2", 80), stop(1, "a1", "t1", 20)])],
      }),
      assetsById
    );
    const [tech] = plan.technicians;
    expect(tech.name).toBe("Alex");
    expect(tech.totalMinutes).toBe(100);
    expect(tech.stops.map((s) => s.asset?.name)).toEqual(["Pump A", "Press B"]);
    expect(tech.stops[0]).toMatchObject({ priority: "high", scheduledDate: "2026-10-12", arrivalMinutes: 20 });
    expect(plan.unassigned).toEqual([]);
  });

  it("flags a part as short only for the assets the shortage affects", () => {
    const part = { part_id: "bearing", part_name: "Bearing 6204", quantity: 2 };
    const gap = {
      part_id: "bearing",
      part_name: "Bearing 6204",
      needed_quantity: 2,
      available_quantity: 0,
      shortage_count: 2,
      lead_time_days: 5,
      impacted_asset_ids: ["a1"],
    };
    const plan = buildWorkPlan(
      state({
        maintenance_schedule: [task("t1", "a1", [part]), task("t2", "a2", [part])],
        inventory_gaps: [gap],
        dispatch_routes: [route([stop(1, "a1", "t1"), stop(2, "a2", "t2")])],
      }),
      assetsById
    );
    const [first, second] = plan.technicians[0].stops;
    expect(first.parts[0].shortage?.lead_time_days).toBe(5);
    expect(second.parts[0].shortage).toBeNull();
  });

  it("treats a gap with no listed assets as affecting every asset that needs the part", () => {
    const part = { part_id: "fuse", part_name: "Fuse", quantity: 1 };
    const plan = buildWorkPlan(
      state({
        maintenance_schedule: [task("t1", "a1", [part])],
        inventory_gaps: [
          {
            part_id: "fuse",
            part_name: "Fuse",
            needed_quantity: 1,
            available_quantity: 0,
            shortage_count: 1,
            lead_time_days: 0,
            impacted_asset_ids: [],
          },
        ],
        dispatch_routes: [route([stop(1, "a1", "t1")])],
      }),
      assetsById
    );
    expect(plan.technicians[0].stops[0].parts[0].shortage).not.toBeNull();
  });

  it("lists scheduled tasks nobody is routed to, so they cannot silently never happen", () => {
    const plan = buildWorkPlan(
      state({
        maintenance_schedule: [task("t1", "a1"), task("t9", "a2")],
        dispatch_routes: [route([stop(1, "a1", "t1")])],
      }),
      assetsById
    );
    expect(plan.unassigned).toEqual([{ taskId: "t9", assetId: "a2", assetName: "Press B", priority: "high" }]);
  });

  it("keeps a stop whose asset was deleted since the run, with a null asset", () => {
    const plan = buildWorkPlan(
      state({
        maintenance_schedule: [task("t1", "gone")],
        dispatch_routes: [route([stop(1, "gone", "t1")])],
      }),
      assetsById
    );
    expect(plan.technicians[0].stops[0].asset).toBeNull();
  });

  it("copes with a snapshot that has no routes, schedule or gaps (null fields)", () => {
    expect(buildWorkPlan(state({}), assetsById)).toEqual({ technicians: [], unassigned: [] });
  });

  it("keeps a stop whose task is missing from the schedule, without a priority", () => {
    const plan = buildWorkPlan(state({ dispatch_routes: [route([stop(1, "a1", "unknown-task")])] }), assetsById);
    expect(plan.technicians[0].stops[0]).toMatchObject({ priority: null, scheduledDate: null, parts: [] });
  });
});

describe("formatMinutes", () => {
  it.each([
    [0, "0 min"],
    [44.6, "45 min"],
    [60, "1 h"],
    [75, "1 h 15 min"],
    [135.4, "2 h 15 min"],
  ])("%s -> %s", (minutes, text) => {
    expect(formatMinutes(minutes)).toBe(text);
  });
});

describe("planAge", () => {
  const now = new Date("2026-10-12T12:00:00Z");

  it("is fresh within a day and stale after it", () => {
    expect(planAge("2026-10-12T00:00:00Z", now)).toMatchObject({ hours: 12, stale: false });
    expect(planAge("2026-10-11T12:00:00Z", now).stale).toBe(false);
    expect(planAge("2026-10-11T11:59:00Z", now).stale).toBe(true);
  });

  it("never reports a negative age (clock skew)", () => {
    expect(planAge("2026-10-12T13:00:00Z", now).hours).toBe(0);
  });
});
