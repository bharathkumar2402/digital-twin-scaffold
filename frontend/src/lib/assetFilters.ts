import type { AssetHealthRow } from "./assetHealth";
import { assetStatus } from "./assetStatus";

export type QuickFilter = "all" | "attention" | "alarms" | "unscored";

export const QUICK_FILTERS: { key: QuickFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "attention", label: "Needs attention" },
  { key: "alarms", label: "Vibration alarms" },
  { key: "unscored", label: "Not scored" },
];

// Anything that is not a known filter name (a stale or hand-edited URL) means "all".
export function parseQuickFilter(value: string | null): QuickFilter {
  return QUICK_FILTERS.some((f) => f.key === value) ? (value as QuickFilter) : "all";
}

export function applyQuickFilter(rows: AssetHealthRow[], filter: QuickFilter): AssetHealthRow[] {
  switch (filter) {
    case "attention":
      return rows.filter((row) => {
        const key = assetStatus(row).key;
        return key === "critical" || key === "warning" || key === "stale";
      });
    case "alarms":
      return rows.filter((row) => row.vibration?.alarm === true);
    case "unscored":
      return rows.filter((row) => row.risk === null);
    case "all":
      return rows;
  }
}

// Case-insensitive match on asset name or type; blank matches everything.
export function searchRows(rows: AssetHealthRow[], query: string): AssetHealthRow[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return rows;
  return rows.filter(
    (row) =>
      row.asset.name.toLowerCase().includes(needle) || row.asset.type.toLowerCase().includes(needle)
  );
}

// ?floor=N -> the floor level if the facility really has it, else null (all floors).
export function parseFloorParam(value: string | null, levels: number[]): number | null {
  if (value === null || value.trim() === "") return null;
  const n = Number(value);
  return Number.isInteger(n) && levels.includes(n) ? n : null;
}
