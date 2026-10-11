import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { AssetHealthTable } from "../components/AssetHealthTable";
import { AssetPanel } from "../components/AssetPanel";
import { EmptyState } from "../components/EmptyState";
import { FloorFilter } from "../components/FloorFilter";
import { HealthGate } from "../components/HealthGate";
import { Section } from "../components/Section";
import { useAssetDependencies } from "../hooks/useAssetDependencies";
import { useFacilityHealth } from "../hooks/useFacilityHealth";
import { assetRowButtonId, filterByFloor } from "../lib/assetHealth";
import {
  applyQuickFilter,
  parseFloorParam,
  parseQuickFilter,
  QUICK_FILTERS,
  searchRows,
} from "../lib/assetFilters";
import type { Asset } from "../types/api";

// Every asset with its health verdict. Filters (floor, quick filter) live in the URL so a view can
// be reloaded or shared; the search text is local because it changes on every keystroke.
export function FacilityAssetsPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const health = useFacilityHealth(facilityId);
  const dependenciesQuery = useAssetDependencies(facilityId);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const floor = parseFloorParam(
    searchParams.get("floor"),
    health.options.map((o) => o.level)
  );
  const floorLabel = health.options.find((o) => o.level === floor)?.label ?? null;
  const quick = parseQuickFilter(searchParams.get("show"));

  const floorRows = useMemo(() => filterByFloor(health.allRows, floor), [health.allRows, floor]);
  const rows = useMemo(
    () => searchRows(applyQuickFilter(floorRows, quick), query),
    [floorRows, quick, query]
  );
  // Derived from the visible rows, so a selection that is filtered out simply has no panel.
  const selectedRow = rows.find((row) => row.asset.id === selectedAssetId) ?? null;

  function setParam(key: string, value: string | null): void {
    setSelectedAssetId(null);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === null) next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true }
    );
  }

  // Each floor has its own map; an asset is shown on the map of the floor it is on.
  function mapHrefFor(asset: Asset): string | null {
    const floorMap = health.facility?.floors.find((f) => f.level === asset.floor_level);
    return floorMap ? `/facilities/${facilityId}/map/${floorMap.map_upload_id}?asset=${asset.id}` : null;
  }

  function closePanel(): void {
    const id = selectedAssetId;
    setSelectedAssetId(null);
    if (id !== null) document.getElementById(assetRowButtonId(id))?.focus();
  }

  function clearFilters(): void {
    setQuery("");
    setParam("show", null);
  }

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h2>Assets</h2>
          <p>Every machine with a plain-language verdict. Click one for its details.</p>
        </div>

        <Section title="Asset health">
          <HealthGate health={health} facilityId={facilityId}>
            {health.options.length > 1 && (
              <FloorFilter
                floors={health.floorHealth}
                totalAssets={health.allRows.length}
                selected={floor}
                onSelect={(level) => setParam("floor", level === null ? null : String(level))}
              />
            )}

            <div className="filter-bar">
              <input
                type="search"
                className="input"
                aria-label="Search assets"
                placeholder="Search by name or type"
                value={query}
                onChange={(event) => {
                  setSelectedAssetId(null);
                  setQuery(event.target.value);
                }}
              />
              <div role="group" aria-label="Quick filter" className="quick-filters">
                {QUICK_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className="btn btn-sm"
                    aria-pressed={quick === f.key}
                    onClick={() => setParam("show", f.key === "all" ? null : f.key)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            <p className="sr-only" aria-live="polite">
              Showing {rows.length} of {health.allRows.length} assets{floorLabel ? ` on ${floorLabel}` : ""}
            </p>

            {floorRows.length === 0 ? (
              <EmptyState
                title={`No assets on ${floorLabel ?? "this floor"}`}
                action={
                  <button type="button" className="btn btn-sm" onClick={() => setParam("floor", null)}>
                    Show all floors
                  </button>
                }
              >
                This floor has a floor plan but no assets placed on it.
              </EmptyState>
            ) : rows.length === 0 ? (
              <EmptyState
                title="No assets match"
                action={
                  <button type="button" className="btn btn-sm" onClick={clearFilters}>
                    Clear filters
                  </button>
                }
              >
                Nothing matches the current search and filter.
              </EmptyState>
            ) : (
              <div className={selectedRow ? "results-split" : undefined}>
                <AssetHealthTable rows={rows} selectedAssetId={selectedAssetId} onSelect={setSelectedAssetId} />
                {selectedRow && (
                  <AssetPanel
                    facilityId={facilityId}
                    row={selectedRow}
                    dependencies={dependenciesQuery.data ?? []}
                    assetsById={health.assetsById}
                    onJump={(asset) => {
                      const href = mapHrefFor(asset);
                      if (href) navigate(href);
                    }}
                    mapHref={mapHrefFor(selectedRow.asset)}
                    onClose={closePanel}
                  />
                )}
              </div>
            )}
          </HealthGate>
        </Section>
      </div>
    </AppShell>
  );
}
