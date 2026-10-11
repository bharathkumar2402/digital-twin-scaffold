import type { FloorHealth } from "../lib/assetHealth";

interface FloorFilterProps {
  floors: FloorHealth[];
  totalAssets: number;
  // null = all floors
  selected: number | null;
  onSelect: (level: number | null) => void;
}

function Chip({
  name,
  assets,
  high,
  alarms,
  pressed,
  onClick,
}: {
  name: string;
  assets: number;
  high: number;
  alarms: number;
  pressed: boolean;
  onClick: () => void;
}): React.JSX.Element {
  return (
    <button type="button" className="floor-chip" aria-pressed={pressed} onClick={onClick}>
      <span className="floor-chip-name">{name}</span>
      <span className="floor-chip-meta">
        {assets} {assets === 1 ? "asset" : "assets"}
      </span>
      <span className={`floor-chip-meta ${high + alarms > 0 ? "floor-chip-warn" : ""}`}>
        {high} high risk · {alarms} {alarms === 1 ? "alarm" : "alarms"}
      </span>
    </button>
  );
}

// Doubles as the per-floor summary: each chip shows that floor's counts.
export function FloorFilter({ floors, totalAssets, selected, onSelect }: FloorFilterProps): React.JSX.Element {
  return (
    <div className="floor-filter" role="group" aria-label="Filter by floor">
      <Chip
        name="All floors"
        assets={totalAssets}
        high={floors.reduce((sum, f) => sum + f.highRisk, 0)}
        alarms={floors.reduce((sum, f) => sum + f.vibrationAlarms, 0)}
        pressed={selected === null}
        onClick={() => onSelect(null)}
      />
      {floors.map((floor) => (
        <Chip
          key={floor.level}
          name={floor.label}
          assets={floor.monitored}
          high={floor.highRisk}
          alarms={floor.vibrationAlarms}
          pressed={selected === floor.level}
          onClick={() => onSelect(floor.level)}
        />
      ))}
    </div>
  );
}
