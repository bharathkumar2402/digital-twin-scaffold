import type { Role } from "../types/api";
import { canRunAnalyses } from "./roles";

export type TabKey = "overview" | "work" | "map" | "assets" | "analysis";

// Which facility tabs a role is shown, in display order. Hiding a tab is a convenience only - a
// direct URL still opens the page, and the backend enforces what a role may actually do.
//  - technician: starts from their work, then where it is, then the assets
//  - managers/admins: everything, including Analysis (running what-ifs)
//  - viewer: read-only views
//  - unknown role: the least-privileged set
export function tabsForRole(role: Role | null): TabKey[] {
  if (role === null) return ["overview", "map", "assets"];
  if (role === "technician") return ["work", "map", "assets", "overview"];
  const tabs: TabKey[] = ["overview", "map", "assets", "work"];
  return canRunAnalyses(role) ? [...tabs, "analysis"] : tabs;
}

// The tab a facility opens on for this role.
export function landingTab(role: Role | null): TabKey {
  return role === "technician" ? "work" : "overview";
}

export const TAB_LABEL: Record<TabKey, string> = {
  overview: "Overview",
  work: "Work",
  map: "Map",
  assets: "Assets",
  analysis: "Analysis",
};
