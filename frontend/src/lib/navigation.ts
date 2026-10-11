import type { Role } from "../types/api";
import { canRunAnalyses } from "./roles";

export type TabKey = "overview" | "map" | "assets" | "analysis";

// Which facility tabs a role is shown, in display order. Analysis (running what-ifs) is for the
// roles allowed to run it; an unknown role gets the least-privileged set. Hiding a tab is a
// convenience only - a direct URL still opens the page, and the backend enforces what a role may do.
export function tabsForRole(role: Role | null): TabKey[] {
  const base: TabKey[] = ["overview", "map", "assets"];
  return canRunAnalyses(role) ? [...base, "analysis"] : base;
}

export const TAB_LABEL: Record<TabKey, string> = {
  overview: "Overview",
  map: "Map",
  assets: "Assets",
  analysis: "Analysis",
};
