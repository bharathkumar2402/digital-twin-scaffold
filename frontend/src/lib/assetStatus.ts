// One plain-language verdict per asset, shared by every screen so the table, map, overview and
// technician view never disagree. Thresholds are the app's existing ones: risk >= 67 is the
// map's "high" band, and <= 30 cycles of remaining life is what the KPI tiles call urgent.
import { formatRul, isStale, riskBand, type AssetHealthRow } from "./assetHealth";

export const RUL_URGENT_CYCLES = 30;

export type StatusKey = "critical" | "warning" | "stale" | "healthy" | "unknown";

export interface HealthStatus {
  key: StatusKey;
  label: string;
  // What a person should do next.
  action: string;
  // Why, in sentences (never raw field names).
  reasons: string[];
  // Sort key: most urgent first.
  rank: number;
}

const RANK: Record<StatusKey, number> = { critical: 0, warning: 1, stale: 2, unknown: 3, healthy: 4 };

// "3 min ago", "2 h ago", "3 days ago".
export function formatAge(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

export function assetStatus(row: AssetHealthRow): HealthStatus {
  const band = row.risk === null ? null : riskBand(row.risk);
  const urgentRul = row.rulCycles !== null && row.rulCycles <= RUL_URGENT_CYCLES;
  const alarm = row.vibration?.alarm === true;

  const stale = isStale(row);
  const reasons: string[] = [];
  if (stale && row.readingAgeMs !== null && row.readingAgeMs !== undefined) {
    reasons.push(`no sensor reading for ${formatAge(row.readingAgeMs)}`);
  }
  if (row.risk !== null) reasons.push(`Risk score ${row.risk.toFixed(1)} out of 100 (${band})`);
  if (row.rulCycles !== null) {
    reasons.push(`about ${formatRul(row.rulCycles)} cycles of life left`);
  }
  if (alarm) {
    const v = row.vibration;
    reasons.push(
      v && v.alarmChannels !== null && v.channelsScored !== null
        ? `vibration alarm on ${v.alarmChannels} of ${v.channelsScored} sensors`
        : "vibration alarm"
    );
  }
  if (row.risk === null && row.rulCycles === null && !alarm && !stale) {
    reasons.push("the risk model has not scored this asset");
  }

  let key: StatusKey;
  let label: string;
  let action: string;
  if (band === "high" || urgentRul) {
    key = "critical";
    label = "Failing soon";
    action = "Schedule maintenance now";
  } else if (stale) {
    // The score below may be old news: a quiet sensor is never reported as Healthy.
    key = "stale";
    label = "No recent data";
    action = "Check the sensor or gateway";
  } else if (alarm) {
    key = "warning";
    label = "Vibration warning";
    action = "Inspect at the next opportunity";
  } else if (band === "medium") {
    key = "warning";
    label = "Watch closely";
    action = "Inspect at the next opportunity";
  } else if (band === "low") {
    key = "healthy";
    label = "Healthy";
    action = "No action needed";
  } else {
    key = "unknown";
    label = "Not enough data";
    action = "Needs the sensor readings the risk model uses";
  }
  return { key, label, action, reasons, rank: RANK[key] };
}
