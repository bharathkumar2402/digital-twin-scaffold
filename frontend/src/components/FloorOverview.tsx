import { Link } from "react-router-dom";

import type { FloorHealth } from "../lib/assetHealth";

function Card({
  to,
  name,
  assets,
  high,
  alarms,
}: {
  to: string;
  name: string;
  assets: number;
  high: number;
  alarms: number;
}): React.JSX.Element {
  return (
    <Link to={to} className="floor-chip floor-link">
      <span className="floor-chip-name">{name}</span>
      <span className="floor-chip-meta">
        {assets} {assets === 1 ? "asset" : "assets"}
      </span>
      <span className={`floor-chip-meta ${high + alarms > 0 ? "floor-chip-warn" : ""}`}>
        {high} high risk · {alarms} {alarms === 1 ? "alarm" : "alarms"}
      </span>
    </Link>
  );
}

// One card per floor; each opens the Assets tab filtered to that floor.
export function FloorOverview({
  facilityId,
  floors,
  totalAssets,
}: {
  facilityId: string;
  floors: FloorHealth[];
  totalAssets: number;
}): React.JSX.Element {
  const assetsPath = `/facilities/${facilityId}/assets`;
  return (
    <nav className="floor-filter" aria-label="Floors">
      <Card
        to={assetsPath}
        name="All floors"
        assets={totalAssets}
        high={floors.reduce((sum, f) => sum + f.highRisk, 0)}
        alarms={floors.reduce((sum, f) => sum + f.vibrationAlarms, 0)}
      />
      {floors.map((floor) => (
        <Card
          key={floor.level}
          to={`${assetsPath}?floor=${floor.level}`}
          name={floor.label}
          assets={floor.monitored}
          high={floor.highRisk}
          alarms={floor.vibrationAlarms}
        />
      ))}
    </nav>
  );
}
