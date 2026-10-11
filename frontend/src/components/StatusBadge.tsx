import type { AssetStatus, StatusKey } from "../lib/assetStatus";

const PILL: Record<StatusKey, string> = {
  critical: "pill-high",
  warning: "pill-medium",
  healthy: "pill-low",
  unknown: "pill-neutral",
};

export function StatusBadge({ status }: { status: AssetStatus }): React.JSX.Element {
  return (
    <span className={`pill ${PILL[status.key]}`} title={`${status.reasons.join("; ")}. ${status.action}.`}>
      {status.label}
    </span>
  );
}
