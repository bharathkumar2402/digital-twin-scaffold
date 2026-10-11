// One plain-language verdict per asset, shared by every screen so the table, map, overview and
// technician view never disagree. Thresholds are the app's existing ones: risk >= 67 is the
// map's "high" band, and <= 30 cycles of remaining life is what the KPI tiles call urgent.
import { formatRul, riskBand, type AssetHealthRow } from "./assetHealth";

export const RUL_URGENT_CYCLES = 30;

export type StatusKey = "critical" | "warning" | "healthy" | "unknown";

export interface AssetStatus {
  key: StatusKey;
  label: string;
  // What a person should do next.
  action: string;
  // Why, in sentences (never raw field names).
  reasons: string[];
  // Sort key: most urgent first.
  rank: number;
}

const RANK: Record<StatusKey, number> = { critical: 0, warning: 1, unknown: 2, healthy: 3 };

export function assetStatus(row: AssetHealthRow): AssetStatus {
  const band = row.risk === null ? null : riskBand(row.risk);
  const urgentRul = row.rulCycles !== null && row.rulCycles <= RUL_URGENT_CYCLES;
  const alarm = row.vibration?.alarm === true;

  const reasons: string[] = [];
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
  if (row.risk === null && row.rulCycles === null && !alarm) {
    reasons.push("the risk model has not scored this asset");
  }

  let key: StatusKey;
  let label: string;
  let action: string;
  if (band === "high" || urgentRul) {
    key = "critical";
    label = "Failing soon";
    action = "Schedule maintenance now";
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
