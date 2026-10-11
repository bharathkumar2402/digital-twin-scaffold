// Turns an analysis run's raw output (routes, schedule, inventory gaps) into what a technician
// needs on a phone: who goes where in what order, what to bring, and what is short.
import type {
  AgentRunState,
  AgentRunSummary,
  Asset,
  InventoryGap,
  MaintenanceItem,
  RiskTier,
} from "../types/api";

export interface PartNeed {
  partName: string;
  quantity: number;
  // Set when stock does not cover this part for this asset.
  shortage: InventoryGap | null;
}

export interface PlanStop {
  stopNumber: number;
  assetId: string;
  // null when the asset is no longer in the facility (deleted since the run).
  asset: Asset | null;
  taskId: string;
  priority: RiskTier | null;
  scheduledDate: string | null;
  arrivalMinutes: number;
  serviceMinutes: number;
  parts: PartNeed[];
}

export interface TechnicianPlan {
  technicianId: string;
  name: string;
  stops: PlanStop[];
  totalMinutes: number;
}

export interface UnassignedTask {
  taskId: string;
  assetId: string;
  assetName: string;
  priority: RiskTier;
}

export interface WorkPlan {
  technicians: TechnicianPlan[];
  unassigned: UnassignedTask[];
}

// The newest run that finished normally. The API returns newest first; a failed or halted run has no
// trustworthy plan, so it is skipped rather than shown.
export function latestCompletedRun(runs: AgentRunSummary[]): AgentRunSummary | null {
  return runs.find((run) => run.status === "completed") ?? null;
}

export function buildWorkPlan(state: AgentRunState, assetsById: Map<string, Asset>): WorkPlan {
  const schedule = state.maintenance_schedule ?? [];
  const tasksById = new Map<string, MaintenanceItem>(schedule.map((task) => [task.item_id, task]));
  const gaps = state.inventory_gaps ?? [];

  const technicians: TechnicianPlan[] = (state.dispatch_routes ?? []).map((route) => ({
    technicianId: route.technician_id,
    name: route.technician_name,
    totalMinutes: route.total_route_duration_minutes,
    stops: [...route.assigned_stops]
      .sort((a, b) => a.stop_number - b.stop_number)
      .map((stop) => {
        const task = tasksById.get(stop.task_id) ?? null;
        return {
          stopNumber: stop.stop_number,
          assetId: stop.asset_id,
          asset: assetsById.get(stop.asset_id) ?? null,
          taskId: stop.task_id,
          priority: task?.priority ?? null,
          scheduledDate: task?.scheduled_date ?? null,
          arrivalMinutes: stop.estimated_arrival_minutes,
          serviceMinutes: stop.service_duration_minutes,
          parts: (task?.required_parts ?? []).map((part) => ({
            partName: part.part_name,
            quantity: part.quantity,
            shortage:
              gaps.find(
                (gap) =>
                  gap.part_id === part.part_id &&
                  gap.shortage_count > 0 &&
                  (gap.impacted_asset_ids.length === 0 || gap.impacted_asset_ids.includes(stop.asset_id))
              ) ?? null,
          })),
        };
      }),
  }));

  // A scheduled task nobody is routed to is work that would silently never happen.
  const routed = new Set(technicians.flatMap((t) => t.stops.map((s) => s.taskId)));
  const unassigned = schedule
    .filter((task) => !routed.has(task.item_id))
    .map((task) => ({
      taskId: task.item_id,
      assetId: task.asset_id,
      assetName: assetsById.get(task.asset_id)?.name ?? "Unknown asset",
      priority: task.priority,
    }));

  return { technicians, unassigned };
}

export function formatMinutes(minutes: number): string {
  const total = Math.round(minutes);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

export const STALE_PLAN_HOURS = 24;

// How old the plan is, and whether it is old enough to warn about.
export function planAge(createdAt: string, now: Date = new Date()): { hours: number; stale: boolean } {
  const hours = Math.max(0, (now.getTime() - new Date(createdAt).getTime()) / 3_600_000);
  return { hours, stale: hours > STALE_PLAN_HOURS };
}
