import type { AssetStatus } from "../types/api";

// The asset's operating state (operational / maintenance / offline). Not to be confused with the
// health verdict from lib/assetStatus.ts (Failing soon, Healthy, ...), shown by StatusBadge.
export function OperatingStateBadge({ state }: { state: AssetStatus }): React.JSX.Element {
  return (
    <span className={`status-badge status-${state}`}>
      <span className="status-dot" />
      {state}
    </span>
  );
}
