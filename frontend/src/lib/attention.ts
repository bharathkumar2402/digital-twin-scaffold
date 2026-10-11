import type { AssetHealthRow } from "./assetHealth";
import { assetStatus } from "./assetStatus";

export interface Attention {
  // The most urgent assets, worst first, at most `limit` of them.
  top: AssetHealthRow[];
  // How many assets need attention in total (can be more than `top.length`).
  total: number;
}

// Which assets to look at first. Only failing or warning assets qualify - never healthy or
// unscored ones. Order: status urgency, then higher risk, then shorter remaining life, then name
// (so the list is stable between renders).
export function topAttention(rows: AssetHealthRow[], limit = 5): Attention {
  const needing = rows
    .map((row) => ({ row, status: assetStatus(row) }))
    .filter(({ status }) => status.key === "critical" || status.key === "warning");

  needing.sort((a, b) => {
    if (a.status.rank !== b.status.rank) return a.status.rank - b.status.rank;
    const riskA = a.row.risk ?? -1;
    const riskB = b.row.risk ?? -1;
    if (riskA !== riskB) return riskB - riskA;
    const rulA = a.row.rulCycles ?? Infinity;
    const rulB = b.row.rulCycles ?? Infinity;
    if (rulA !== rulB) return rulA - rulB;
    return a.row.asset.name.localeCompare(b.row.asset.name);
  });

  return { top: needing.slice(0, Math.max(0, limit)).map((n) => n.row), total: needing.length };
}

// The newest time any asset was scored, or null if none was.
export function latestScoredAt(rows: AssetHealthRow[]): string | null {
  let latest: string | null = null;
  for (const row of rows) {
    if (row.scoredAt !== null && (latest === null || row.scoredAt > latest)) latest = row.scoredAt;
  }
  return latest;
}

// Ready-made what-if questions for the machines most worth asking about: the ones needing attention,
// or, if nothing does, simply the riskiest assets (rows arrive worst first).
export function suggestedQuestions(rows: AssetHealthRow[], limit = 3): string[] {
  const { top } = topAttention(rows, limit);
  const picks = top.length > 0 ? top : rows.slice(0, limit);
  return picks.map((row) => `What if ${row.asset.name} fails?`);
}
