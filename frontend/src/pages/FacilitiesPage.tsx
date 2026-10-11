import { Link } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { useFacilities } from "../hooks/useFacilities";

export function FacilitiesPage(): React.JSX.Element {
  const { data, isPending, isError } = useFacilities();

  return (
    <AppShell>
      <div className="page">
        <div className="page-head">
          <h2>Your facilities</h2>
          <p>Pick a facility to open its map or its overview.</p>
        </div>

        {isPending && <p>Loading facilities...</p>}
        {isError && (
          <p role="alert" className="banner-error">
            Could not load your facilities.
          </p>
        )}
        {data && data.length === 0 && (
          <p className="empty-state">There are no facilities on this account yet.</p>
        )}

        <div className="facility-grid">
          {data?.map((facility) => (
            <article key={facility.id} className="facility-card" aria-label={facility.name}>
              <h3>{facility.name}</h3>
              <p className="facility-meta">
                {facility.asset_count} {facility.asset_count === 1 ? "asset" : "assets"}
                {facility.latest_map_upload_id ? "" : " · no floor plan yet"}
              </p>
              <div className="facility-actions">
                {facility.latest_map_upload_id ? (
                  <Link
                    className="btn btn-primary"
                    to={`/facilities/${facility.id}/map/${facility.latest_map_upload_id}`}
                  >
                    Open map
                  </Link>
                ) : (
                  <span className="btn" aria-disabled="true" style={{ opacity: 0.5 }}>
                    Open map
                  </span>
                )}
                <Link className="btn" to={`/facilities/${facility.id}/overview`}>
                  Overview
                </Link>
              </div>
            </article>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
