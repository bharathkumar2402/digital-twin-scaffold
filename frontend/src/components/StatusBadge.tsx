import type { HealthStatus, StatusKey } from "../lib/assetStatus";

const PILL: Record<StatusKey, string> = {
  critical: "pill-high",
  warning: "pill-medium",
  stale: "pill-stale",
  healthy: "pill-low",
  unknown: "pill-neutral",
};

export function StatusBadge({ status }: { status: HealthStatus }): React.JSX.Element {
  return (
    <span className={`pill ${PILL[status.key]}`} title={`${status.reasons.join("; ")}. ${status.action}.`}>
      {status.label}
    </span>
  );
}
