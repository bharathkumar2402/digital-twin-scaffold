import { Link } from "react-router-dom";

import { floorLabel, type FloorBadge } from "../lib/floors";

interface FloorSwitcherProps {
  facilityId: string;
  badges: FloorBadge[];
  currentLevel: number | undefined;
}

// One tab per floor of the facility. Each floor is its own map (its own tile pyramid), so a tab
// is a link to that floor's map. A red count shows how many assets there are in the high-risk
// band: the reason to go to that floor.
export function FloorSwitcher({
  facilityId,
  badges,
  currentLevel,
}: FloorSwitcherProps): React.JSX.Element | null {
  if (badges.length < 2) return null; // a single-floor facility has nothing to switch between

  return (
    <nav className="card floor-switcher" aria-label="Floors">
      {[...badges].reverse().map((badge) => {
        // top floor first, like a building's elevator panel
        const active = badge.level === currentLevel;
        const detail = [
          `${badge.assetCount} assets`,
          badge.highRisk > 0 ? `${badge.highRisk} high risk` : null,
          badge.alarms > 0 ? `${badge.alarms} vibration alarms` : null,
        ]
          .filter(Boolean)
          .join(", ");
        return (
          <Link
            key={badge.level}
            to={`/facilities/${facilityId}/map/${badge.mapUploadId}`}
            className={`floor-tab${active ? " floor-tab-active" : ""}`}
            aria-current={active ? "page" : undefined}
            aria-label={`${badge.name}: ${detail}`}
            title={`${badge.name} - ${detail}`}
          >
            <span className="floor-tab-level">{floorLabel(badge.level)}</span>
            <span className="floor-tab-name">{badge.name}</span>
            {badge.highRisk > 0 && (
              <span className="floor-tab-badge" aria-hidden="true">
                {badge.highRisk}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
