import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AgentAnalysisPanel } from "../components/AgentAnalysisPanel";
import { AppShell } from "../components/AppShell";
import { AssetPanel } from "../components/AssetPanel";
import { AssetHealthTable } from "../components/AssetHealthTable";
import { EmptyState } from "../components/EmptyState";
import { FloorFilter } from "../components/FloorFilter";
import { KpiTiles } from "../components/KpiTiles";
import { ModelCards } from "../components/ModelCards";
import { ResultsSkeleton } from "../components/Skeleton";
import { useAssetDependencies } from "../hooks/useAssetDependencies";
import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { useRiskScores } from "../hooks/useRiskScores";
import type { Asset } from "../types/api";
import {
  assetRowButtonId,
  buildHealthRows,
  filterByFloor,
  floorOptions,
  summarizeByFloor,
  summarizeHealth,
} from "../lib/assetHealth";

// Mirrors the backend's RUN_ROLES in app/api/agent_runs.py; the server still enforces it.
const RUN_ROLES = new Set(["superadmin", "tenant_admin", "facility_manager"]);

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="results-section" aria-label={title}>
      <h3 style={{ marginBottom: 10 }}>{title}</h3>
      {children}
    </section>
  );
}

export function FacilityResultsPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const { role } = useAuth();
  const canRun = role !== null && RUN_ROLES.has(role);
  const [searchParams, setSearchParams] = useSearchParams();

  const facilities = useFacilities();
  const facility = facilities.data?.find((f) => f.id === facilityId) ?? null;
  const assetsQuery = useAssets(facilityId);
  const scoresQuery = useRiskScores(facilityId);
  const dependenciesQuery = useAssetDependencies(facilityId);
  const navigate = useNavigate();
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  const allRows = useMemo(
    () => buildHealthRows(assetsQuery.data ?? [], scoresQuery.data ?? []),
    [assetsQuery.data, scoresQuery.data]
  );
  const options = useMemo(() => floorOptions(allRows, facility?.floors ?? []), [allRows, facility]);
  const floorHealth = useMemo(() => summarizeByFloor(allRows, options), [allRows, options]);

  // The filter lives in the URL (?floor=2) so it survives a reload and can be shared. A missing,
  // malformed or unknown value means "all floors".
  const floorParam = searchParams.get("floor");
  const requested = floorParam === null || floorParam === "" ? NaN : Number(floorParam);
  const floor = Number.isInteger(requested) && options.some((o) => o.level === requested) ? requested : null;
  const floorLabel = options.find((o) => o.level === floor)?.label ?? null;

  const rows = useMemo(() => filterByFloor(allRows, floor), [allRows, floor]);
  const assetNames = useMemo(
    () => new Map((assetsQuery.data ?? []).map((asset) => [asset.id, asset.name])),
    [assetsQuery.data]
  );
  const summary = useMemo(() => summarizeHealth(rows), [rows]);
  // Derived from the filtered rows, so a selection on another floor simply has no drawer.
  const selectedRow = rows.find((row) => row.asset.id === selectedAssetId) ?? null;

  const assetsById = useMemo(
    () => new Map((assetsQuery.data ?? []).map((asset) => [asset.id, asset])),
    [assetsQuery.data]
  );

  // Each floor has its own map; an asset is shown on the map of the floor it is on.
  function mapHrefFor(asset: Asset): string | null {
    const floorMap = facility?.floors.find((f) => f.level === asset.floor_level);
    return floorMap ? `/facilities/${facilityId}/map/${floorMap.map_upload_id}?asset=${asset.id}` : null;
  }

  function selectFloor(level: number | null): void {
    setSelectedAssetId(null);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (level === null) next.delete("floor");
        else next.set("floor", String(level));
        return next;
      },
      { replace: true }
    );
  }

  function closeDrawer(): void {
    const id = selectedAssetId;
    setSelectedAssetId(null);
    if (id !== null) document.getElementById(assetRowButtonId(id))?.focus();
  }

  const failed: { what: string; retry: () => void }[] = [];
  if (assetsQuery.isError) failed.push({ what: "assets", retry: () => void assetsQuery.refetch() });
  if (scoresQuery.isError) failed.push({ what: "risk scores", retry: () => void scoresQuery.refetch() });
  const assets = assetsQuery.data;
  const scores = scoresQuery.data;
  const mapUploadId = facility?.latest_map_upload_id ?? null;

  function healthContent(): React.ReactNode {
    if (failed.length > 0) {
      return (
        <div role="alert" className="error-state">
          <span>Could not load this facility&rsquo;s {failed.map((f) => f.what).join(" or ")}.</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => failed.forEach((f) => f.retry())}
          >
            Retry
          </button>
        </div>
      );
    }
    if (assets === undefined || scores === undefined) return <ResultsSkeleton />;
    if (allRows.length === 0) {
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
    if (scores.length === 0) {
      return (
        <EmptyState title="No risk scores yet">
          This facility has {allRows.length} {allRows.length === 1 ? "asset" : "assets"}, but scoring has not
          run for any of them. Scores appear here once telemetry has been scored.
        </EmptyState>
      );
    }
    return (
      <>
        {options.length > 1 && (
          <FloorFilter
            floors={floorHealth}
            totalAssets={allRows.length}
            selected={floor}
            onSelect={selectFloor}
          />
        )}
        <p className="sr-only" aria-live="polite">
          Showing {rows.length} of {allRows.length} assets{floorLabel ? ` on ${floorLabel}` : ""}
        </p>
        {rows.length === 0 ? (
          <EmptyState
            title={`No assets on ${floorLabel ?? "this floor"}`}
            action={
              <button type="button" className="btn btn-sm" onClick={() => selectFloor(null)}>
                Show all floors
              </button>
            }
          >
            This floor has a floor plan but no assets placed on it.
          </EmptyState>
        ) : (
          <>
            <div className={selectedRow ? "results-split" : undefined}>
              <AssetHealthTable rows={rows} selectedAssetId={selectedAssetId} onSelect={setSelectedAssetId} />
              {selectedRow && (
                <AssetPanel
                  facilityId={facilityId}
                  row={selectedRow}
                  dependencies={dependenciesQuery.data ?? []}
                  assetsById={assetsById}
                  onJump={(asset) => {
                    const href = mapHrefFor(asset);
                    if (href) navigate(href);
                  }}
                  mapHref={mapHrefFor(selectedRow.asset)}
                  onClose={closeDrawer}
                />
              )}
            </div>
          </>
        )}
      </>
    );
  }

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h2>Facility results</h2>
          <p>What the risk, remaining-life and vibration models and the five agents say right now.</p>
        </div>

        {assets !== undefined && scores !== undefined && rows.length > 0 && (
          <KpiTiles summary={summary} scopeLabel={floorLabel} />
        )}

        <Section title="Asset health">{healthContent()}</Section>

        <Section title="Agent analysis">
          <p className="scope-note">Analyses cover the whole facility, whichever floor is selected above.</p>
          <AgentAnalysisPanel facilityId={facilityId} assetNames={assetNames} canRun={canRun} />
        </Section>

        <Section title="About these models">
          <ModelCards />
        </Section>
      </div>
    </AppShell>
  );
}
