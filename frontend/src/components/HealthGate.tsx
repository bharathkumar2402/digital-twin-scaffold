import { Link } from "react-router-dom";

import type { FacilityHealth } from "../hooks/useFacilityHealth";
import { EmptyState } from "./EmptyState";
import { ResultsSkeleton } from "./Skeleton";

// Shows the right thing until the facility's health data is ready to render: an error with Retry
// (naming what failed), a skeleton, or an explanation when there is nothing to show yet.
export function HealthGate({
  health,
  facilityId,
  children,
}: {
  health: FacilityHealth;
  facilityId: string;
  children: React.ReactNode;
}): React.JSX.Element {
  if (health.failed.length > 0) {
    return (
      <div role="alert" className="error-state">
        <span>Could not load this facility&rsquo;s {health.failed.map((f) => f.what).join(" or ")}.</span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => health.failed.forEach((f) => f.retry())}
        >
          Retry
        </button>
      </div>
    );
  }
  if (!health.loaded) return <ResultsSkeleton />;

  const count = health.allRows.length;
  if (count === 0) {
    const mapUploadId = health.facility?.latest_map_upload_id ?? null;
    return (
      <EmptyState
        title="No assets in this facility yet"
        action={
          mapUploadId !== null ? (
            <Link className="btn btn-sm" to={`/facilities/${facilityId}/map/${mapUploadId}`}>
              Open the map
            </Link>
          ) : undefined
        }
      >
        {mapUploadId !== null
          ? "Place assets on the floor plan from the Map tab and their health will appear here."
          : "Upload a floor plan and place assets on it; their health will appear here."}
      </EmptyState>
    );
  }
  if (health.scoreCount === 0) {
    return (
      <EmptyState title="No risk scores yet">
        This facility has {count} {count === 1 ? "asset" : "assets"}, but scoring has not run for any of
        them. Scores appear here once telemetry has been scored.
      </EmptyState>
    );
  }
  return <>{children}</>;
}
