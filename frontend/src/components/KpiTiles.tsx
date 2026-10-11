import type { HealthSummary } from "../lib/assetHealth";

function Tile({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone: "neutral" | "danger" | "ok";
}): React.JSX.Element {
  return (
    <div className={`kpi kpi-${tone}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

// Four numbers that answer "is anything wrong?" before anyone reads the table.
export function KpiTiles({
  summary,
  scopeLabel = null,
}: {
  summary: HealthSummary;
  // Name of the floor the numbers are limited to; null = whole facility.
  scopeLabel?: string | null;
}): React.JSX.Element {
  return (
    <div
      className="kpi-row"
      role="group"
      aria-label={scopeLabel === null ? "Facility summary" : `Summary for ${scopeLabel}`}
    >
      <Tile
        label="Assets monitored"
        value={String(summary.monitored)}
        note={summary.unscored > 0 ? `${summary.unscored} not scored` : "all scored"}
        tone="neutral"
      />
      <Tile
        label="High risk"
        value={String(summary.highRisk)}
        note="risk 67 or more"
        tone={summary.highRisk > 0 ? "danger" : "ok"}
      />
      <Tile
        label="Vibration alarms"
        value={String(summary.vibrationAlarms)}
        note="sustained anomaly"
        tone={summary.vibrationAlarms > 0 ? "danger" : "ok"}
      />
      <Tile
        label="Shortest remaining life"
        value={summary.shortestRul ? `${summary.shortestRul.cycles.toFixed(1)} cycles` : "\u2014"}
        note={summary.shortestRul ? summary.shortestRul.assetName : "no RUL available"}
        tone={summary.shortestRul !== null && summary.shortestRul.cycles <= 30 ? "danger" : "neutral"}
      />
    </div>
  );
}
