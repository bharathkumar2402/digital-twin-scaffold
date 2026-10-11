import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";

interface AppShellProps {
  // Set on facility pages; shows the facility name and the Map / Results tabs.
  facilityId?: string;
  children: ReactNode;
}

function navClass({ isActive }: { isActive: boolean }): string {
  return `nav-link${isActive ? " nav-active" : ""}`;
}

// The frame shared by every signed-in page: brand, the current facility with its Map and
// Results tabs, and Sign out. The facility's name and newest map come from GET /facilities, so
// the tabs work without ids having to be threaded through URLs.
export function AppShell({ facilityId, children }: AppShellProps): React.JSX.Element {
  const { logout } = useAuth();
  const facilities = useFacilities();
  const facility = facilityId ? facilities.data?.find((f) => f.id === facilityId) : undefined;
  const mapUploadId = facility?.latest_map_upload_id ?? null;

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="brand" aria-label="All facilities">
          <span className="brand-mark">DT</span>
          <span>Digital Twin</span>
        </Link>

        {facilityId && (
          <nav className="topnav" aria-label="Facility">
            <span className="crumb">{facility?.name ?? "Facility"}</span>
            {mapUploadId ? (
              <NavLink to={`/facilities/${facilityId}/map/${mapUploadId}`} className={navClass}>
                Map
              </NavLink>
            ) : (
              <span className="nav-link nav-disabled" title="This facility has no floor plan yet">
                Map
              </span>
            )}
            <NavLink to={`/facilities/${facilityId}/results`} className={navClass}>
              Results
            </NavLink>
          </nav>
        )}

        <div className="topbar-spacer" />
        <button type="button" className="btn-topbar" onClick={logout}>
          Sign out
        </button>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
