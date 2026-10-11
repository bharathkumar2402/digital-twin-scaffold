import { useMemo, useState } from "react";

import { RISK_BAND_COLOR, riskBand, type AssetHealthRow } from "../lib/assetHealth";
import { filterRows, floorLabel, groupByFloor, type AssetFilter } from "../lib/floors";
import type { Asset, FloorSummary } from "../types/api";

interface AssetBrowserProps {
  rows: AssetHealthRow[];
  floors: FloorSummary[];
  currentLevel: number | undefined;
  isLoading: boolean;
  onPick: (asset: Asset) => void;
}

const FILTERS: { value: AssetFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "high", label: "High risk" },
  { value: "alarm", label: "Vibration alarm" },
];

// The sidebar's asset list for a multi-floor facility: search, a couple of quick filters, and the
// assets grouped by floor (the floor being viewed first). Picking one on another floor switches
// floors - that is the caller's job (onPick).
export function AssetBrowser({
  rows,
  floors,
  currentLevel,
  isLoading,
  onPick,
}: AssetBrowserProps): React.JSX.Element {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<AssetFilter>("all");

  const matching = useMemo(() => filterRows(rows, { query, filter }), [rows, query, filter]);
  const groups = useMemo(() => {
    const all = groupByFloor(matching, floors);
    // the floor on screen first, the rest in building order
    return [...all].sort((a, b) => Number(b.level === currentLevel) - Number(a.level === currentLevel));
  }, [matching, floors, currentLevel]);

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h3 style={{ fontSize: 15 }}>Assets</h3>
        <p style={{ fontSize: 12.5, margin: 0 }} aria-live="polite">
          {matching.length === rows.length
            ? `${rows.length} in this facility`
            : `${matching.length} of ${rows.length} match`}
        </p>
      </div>

      <input
        type="search"
        className="input"
        placeholder="Search by name or type"
        aria-label="Search assets"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        style={{ marginBottom: 8 }}
      />
      <div className="chip-row" role="group" aria-label="Filter assets">
        {FILTERS.map((item) => (
          <button
            key={item.value}
            type="button"
            className={`chip${filter === item.value ? " chip-active" : ""}`}
            aria-pressed={filter === item.value}
            onClick={() => setFilter(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {isLoading && <p style={{ fontSize: 13 }}>Loading assets...</p>}
      {!isLoading && matching.length === 0 && (
        <p style={{ fontSize: 13 }}>
          {rows.length === 0 ? "No assets yet." : "No asset matches that search."}
        </p>
      )}

      {groups.map((group) => (
        <section key={group.level} aria-label={group.name}>
          <h4 className="group-heading">
            <span>
              {floorLabel(group.level)} · {group.name}
            </span>
            <span>{group.level === currentLevel ? "this floor" : `${group.rows.length}`}</span>
          </h4>
          <ul className="asset-list">
            {group.rows.map((row) => {
              const color =
                row.risk === null ? "#9e9e9e" : RISK_BAND_COLOR[riskBand(row.risk)];
              return (
                <li key={row.asset.id}>
                  <button type="button" className="asset-row" onClick={() => onPick(row.asset)}>
                    <span className="asset-row-dot" style={{ background: color }} aria-hidden="true" />
                    <span className="asset-row-main">
                      <span className="asset-row-name">{row.asset.name}</span>
                      <span className="asset-row-type">{row.asset.type.replace(/_/g, " ")}</span>
                    </span>
                    {row.vibration?.alarm && (
                      <span className="asset-row-flag" title="Vibration alarm">
                        alarm
                      </span>
                    )}
                    <span className="asset-row-risk">{row.risk === null ? "–" : Math.round(row.risk)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
