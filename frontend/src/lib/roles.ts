import type { Role } from "../types/api";

// Mirrors the backend's RUN_ROLES in app/api/agent_runs.py. This only decides what the UI offers;
// the server enforces it regardless.
const RUN_ROLES: ReadonlySet<Role> = new Set<Role>(["superadmin", "tenant_admin", "facility_manager"]);

export function canRunAnalyses(role: Role | null): boolean {
  return role !== null && RUN_ROLES.has(role);
}
