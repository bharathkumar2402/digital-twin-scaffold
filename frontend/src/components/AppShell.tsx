import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { TAB_LABEL, tabsForRole, type TabKey } from "../lib/navigation";

interface AppShellProps {
  // Set on facility pages; shows the facility name and the tabs this role may see.
  facilityId?: string;
  children: ReactNode;
}

function navClass({ isActive }: { isActive: boolean }): string {
  return `nav-link${isActive ? " nav-active" : ""}`;
}

function tabLink(tab: TabKey, facilityId: string, mapUploadId: string | null): React.JSX.Element {
  if (tab === "map") {
    return mapUploadId ? (
      <NavLink key={tab} to={`/facilities/${facilityId}/map/${mapUploadId}`} className={navClass}>
        {TAB_LABEL[tab]}
      </NavLink>
    ) : (
      <span key={tab} className="nav-link nav-disabled" title="This facility has no floor plan yet">
        {TAB_LABEL[tab]}
      </span>
    );
  }
  return (
    <NavLink key={tab} to={`/facilities/${facilityId}/${tab}`} className={navClass}>
      {TAB_LABEL[tab]}
    </NavLink>
  );
}

// The frame shared by every signed-in page: brand, the current facility with its Map and
// Results tabs, and Sign out. The facility's name and newest map come from GET /facilities, so
// the tabs work without ids having to be threaded through URLs.
export function AppShell({ facilityId, children }: AppShellProps): React.JSX.Element {
  const { logout, role } = useAuth();
  const facilities = useFacilities();
  const facility = facilityId ? facilities.data?.find((f) => f.id === facilityId) : undefined;
  const mapUploadId = facility?.latest_map_upload_id ?? null;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <Link to="/" className="brand" title="All facilities">
          <span className="brand-mark" aria-hidden="true">
            DT
          </span>
          <span>Digital Twin</span>
        </Link>

        {facilityId && (
          <nav className="topnav" aria-label="Facility">
            <span className="crumb">{facility?.name ?? "Facility"}</span>
            {tabsForRole(role).map((tab) => tabLink(tab, facilityId, mapUploadId))}
          </nav>
        )}

        <div className="topbar-spacer" />
        <Link to="/about" className="topbar-link">
          About the models
        </Link>
        <button type="button" className="btn-topbar" onClick={logout}>
          Sign out
        </button>
      </header>
      <main id="main-content" className="app-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
