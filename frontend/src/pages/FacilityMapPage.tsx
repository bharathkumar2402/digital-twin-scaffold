import type maplibregl from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { AssetBrowser } from "../components/AssetBrowser";
import { AssetPanel } from "../components/AssetPanel";
import { AssetHoverTip } from "../components/AssetHoverTip";
import { AssetLayer } from "../components/AssetLayer";
import { DependencyLayer } from "../components/DependencyLayer";
import { FacilityMap } from "../components/FacilityMap";
import { FloorSwitcher } from "../components/FloorSwitcher";
import { SelectionLayer } from "../components/SelectionLayer";
import {
  useAssetDependencies,
  useCreateAssetDependency,
  useDeleteAssetDependency,
} from "../hooks/useAssetDependencies";
import { useAuth } from "../hooks/useAuth";
import { useAssets, useCreateAsset, useDeleteAsset, useUpdateAsset } from "../hooks/useAssets";
import { useFacilities } from "../hooks/useFacilities";
import { useFacilityMapUpload } from "../hooks/useFacilityMapUpload";
import { useRiskHistory } from "../hooks/useRiskHistory";
import { useRiskScores } from "../hooks/useRiskScores";
import { useTelemetryFreshness } from "../hooks/useTelemetryFreshness";
import { UpdatedAgo } from "../components/UpdatedAgo";
import { buildHealthRows } from "../lib/assetHealth";
import { latestScoredAt } from "../lib/attention";
import type { ServerClock } from "../lib/updatedAgo";
import { assetStatus } from "../lib/assetStatus";
import { assetBounds } from "../lib/fitAssets";
import { assetsOnFloor, floorBadges, floorOfUpload } from "../lib/floors";
import { pixelToLngLat } from "../lib/mapCoords";
import type { Asset } from "../types/api";

// Mirrors backend/app/core/rbac.py's WRITE_ROLES in app/api/assets.py - technician and
// viewer stay read-only on the map, same restriction as facility map uploads.
const WRITE_ROLES = new Set(["superadmin", "tenant_admin", "facility_manager"]);

// Zoom used when centring on one asset (the whole-floor fit is capped at 3.4).
const FOCUS_ZOOM = 3;

interface NewAssetDraft {
  x: number;
  y: number;
  name: string;
  type: string;
}

function CenteredMessage({
  children,
  isError,
}: {
  children: React.ReactNode;
  isError?: boolean;
}): React.JSX.Element {
  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--color-bg)",
      }}
    >
      <p
        role={isError ? "alert" : undefined}
        style={{
          margin: 0,
          fontSize: 14,
          color: isError ? "var(--color-danger)" : "var(--color-text-muted)",
        }}
      >
        {children}
      </p>
    </div>
  );
}

// No facilities-list endpoint exists on the backend yet (facility CRUD isn't a built
// task in PHASE_PLAN.md's Phase 2 - only the map-upload/status routes are), so this
// page is reached by direct URL with a known facility/upload id rather than through a
// facility picker. That's a real gap, but it's outside 2.5's scope: PHASE_PLAN.md's
// task list doesn't include a facilities-list route, and 2.4 confirmed tile rendering
// the same way - a direct URL hit, not a UI flow.
export function FacilityMapPage(): React.JSX.Element {
  const { facilityId, uploadId } = useParams<{ facilityId: string; uploadId: string }>();
  const { data, isPending, isError, error } = useFacilityMapUpload(facilityId ?? "", uploadId ?? "");
  const { role } = useAuth();
  const canEdit = role !== null && WRITE_ROLES.has(role);

  // Each floor is its own map instance, destroyed when the floor changes. A map is only handed to
  // the layers while the upload it was built for is still the one on screen - otherwise they would
  // touch a destroyed map for a render.
  const [mapState, setMapState] = useState<{ map: maplibregl.Map; uploadId: string } | null>(null);
  const map = mapState && mapState.uploadId === uploadId ? mapState.map : null;
  const handleMapLoad = useCallback(
    (loaded: maplibregl.Map) => setMapState({ map: loaded, uploadId: uploadId ?? "" }),
    [uploadId]
  );
  const [addMode, setAddMode] = useState(false);
  const [linkMode, setLinkMode] = useState(false);
  const [pendingParentId, setPendingParentId] = useState<string | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [newAssetDraft, setNewAssetDraft] = useState<NewAssetDraft | null>(null);

  const assetsQuery = useAssets(facilityId ?? "");
  const createAsset = useCreateAsset(facilityId ?? "");
  const updateAsset = useUpdateAsset(facilityId ?? "");
  const deleteAsset = useDeleteAsset(facilityId ?? "");

  const dependenciesQuery = useAssetDependencies(facilityId ?? "");
  const createDependency = useCreateAssetDependency(facilityId ?? "");
  const deleteDependency = useDeleteAssetDependency(facilityId ?? "");

  const riskScoresQuery = useRiskScores(facilityId ?? "");
  const riskHistory = useRiskHistory(facilityId ?? "");
  const freshnessQuery = useTelemetryFreshness(facilityId ?? "");

  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const facilitiesQuery = useFacilities();
  const floors = useMemo(
    () => facilitiesQuery.data?.find((f) => f.id === facilityId)?.floors ?? [],
    [facilitiesQuery.data, facilityId]
  );

  const allAssets = useMemo(() => assetsQuery.data ?? [], [assetsQuery.data]);
  const allDependencies = useMemo(() => dependenciesQuery.data ?? [], [dependenciesQuery.data]);
  const assetsById = useMemo(() => new Map(allAssets.map((asset) => [asset.id, asset])), [allAssets]);

  // Each floor is its own map. Which floor this one is comes from the upload itself, or from the
  // facility's floor list; if neither says (an old facility), every asset is shown as before.
  const currentLevel: number | undefined =
    data?.floor_level ?? floorOfUpload(floors, uploadId ?? "")?.level;
  const assets = useMemo(
    () => (currentLevel === undefined ? allAssets : assetsOnFloor(allAssets, currentLevel)),
    [allAssets, currentLevel]
  );
  // Only links between assets on this floor are drawn; a link to another floor appears in the
  // asset's detail panel instead (a line cannot join two separate maps).
  const dependencies = useMemo(() => {
    const here = new Set(assets.map((asset) => asset.id));
    return allDependencies.filter(
      (dep) => here.has(dep.parent_asset_id) && here.has(dep.child_asset_id)
    );
  }, [assets, allDependencies]);
  const selectedAsset = assets.find((asset) => asset.id === selectedAssetId) ?? null;

  // Highest-computed_at score per asset - get_latest_risk_scores already returns at
  // most one row per asset, but the map lookup itself doesn't assume that ordering.
  const riskScoresByAssetId = useMemo(() => {
    const scores = riskScoresQuery.data ?? [];
    return new Map(scores.map((riskScore) => [riskScore.asset_id, riskScore.score]));
  }, [riskScoresQuery.data]);
  const healthRows = useMemo(
    () => buildHealthRows(allAssets, riskScoresQuery.data ?? [], freshnessQuery.data),
    [allAssets, riskScoresQuery.data, freshnessQuery.data]
  );
  // Markers of assets that went quiet are drawn in their own slate colour instead of a risk colour
  // (a failing-soon asset keeps its red: assetStatus says "critical" before "stale").
  const staleAssetIds = useMemo(
    () => new Set(healthRows.filter((row) => assetStatus(row).key === "stale").map((r) => r.asset.id)),
    [healthRows]
  );
  const freshnessAsOf = freshnessQuery.data?.as_of;
  const freshnessClock = useMemo<ServerClock | null>(
    () =>
      freshnessAsOf !== undefined && freshnessQuery.dataUpdatedAt
        ? { asOfMs: Date.parse(freshnessAsOf), fetchedAtMs: freshnessQuery.dataUpdatedAt }
        : null,
    [freshnessAsOf, freshnessQuery.dataUpdatedAt]
  );
  const badges = useMemo(() => floorBadges(floors, healthRows), [floors, healthRows]);
  const selectedRow = healthRows.find((row) => row.asset.id === selectedAssetId) ?? null;

  // Open framed on the equipment (not on the whole world), once per map; "Fit view" re-frames.
  const fitToAssets = useCallback(
    (animate: boolean) => {
      const bounds = assetBounds(assets);
      if (map && bounds) {
        // The container may have changed size since the map was created (shell, sidebar), and a
        // fit against a stale size frames the equipment too tightly - re-measure first.
        map.resize();
        map.fitBounds(bounds, {
          padding: { top: 110, bottom: 150, left: 120, right: 120 },
          maxZoom: 3.4,
          duration: animate ? 500 : 0,
        });
      }
    },
    [map, assets]
  );
  // Switching floors creates a new map instance, so "fitted" is tracked per map, not once ever.
  const fittedMapRef = useRef<maplibregl.Map | null>(null);
  // An asset picked from another floor arrives as ?asset=<id>: it is shown selected and centred
  // instead of the whole-floor fit.
  const wantedAssetId = searchParams.get("asset");
  useEffect(() => {
    if (!map || assets.length === 0 || fittedMapRef.current === map) return;
    fittedMapRef.current = map;
    const wanted = wantedAssetId ? assets.find((a) => a.id === wantedAssetId) : undefined;
    if (wanted) {
      setSelectedAssetId(wanted.id);
      map.jumpTo({ center: pixelToLngLat(wanted.x, wanted.y), zoom: FOCUS_ZOOM });
      setSearchParams({}, { replace: true });
    } else {
      fitToAssets(false);
    }
  }, [map, assets, wantedAssetId, fitToAssets, setSearchParams]);

  // A selection belongs to the floor it was made on.
  useEffect(() => {
    setSelectedAssetId((current) => (current && !assets.some((a) => a.id === current) && !wantedAssetId ? null : current));
  }, [assets, wantedAssetId]);

  // Pick an asset from a list: on this floor, select it and bring it into view; on another floor,
  // open that floor's map with the asset preselected.
  const goToAsset = useCallback(
    (asset: Asset) => {
      if (currentLevel === undefined || asset.floor_level === currentLevel) {
        setNewAssetDraft(null);
        setSelectedAssetId(asset.id);
        map?.easeTo({
          center: pixelToLngLat(asset.x, asset.y),
          zoom: Math.max(map.getZoom(), FOCUS_ZOOM),
          duration: 500,
        });
        return;
      }
      const floor = floors.find((f) => f.level === asset.floor_level);
      if (floor) navigate(`/facilities/${facilityId}/map/${floor.map_upload_id}?asset=${asset.id}`);
    },
    [currentLevel, map, floors, navigate, facilityId]
  );

  if (!facilityId || !uploadId) {
    return <CenteredMessage isError>Missing facility or upload id in the URL.</CenteredMessage>;
  }
  if (isPending) {
    return <CenteredMessage>Loading upload status...</CenteredMessage>;
  }
  if (isError) {
    return <CenteredMessage isError>Failed to load upload status: {error.message}</CenteredMessage>;
  }
  if (data.status === "failed" || data.status === "conversion_failed") {
    return <CenteredMessage isError>Floor plan processing failed: {data.status}</CenteredMessage>;
  }
  if (data.status !== "tiled" || !data.tile_url_template) {
    return <CenteredMessage>Processing floor plan ({data.status})...</CenteredMessage>;
  }

  return (
    <AppShell facilityId={facilityId}>
    <div className="map-layout">
      <h1 className="sr-only">Floor map</h1>
      <div className="map-stage">
        <FacilityMap tileUrlTemplate={data.tile_url_template} onMapLoad={handleMapLoad} />
        <DependencyLayer map={map} assets={assets} dependencies={dependencies} />
        <SelectionLayer map={map} asset={selectedAsset} />
        <AssetHoverTip map={map} assets={assets} riskScores={riskScoresByAssetId} />
        <FloorSwitcher facilityId={facilityId} badges={badges} currentLevel={currentLevel} />
        <AssetLayer
          map={map}
          assets={assets}
          riskScores={riskScoresByAssetId}
          staleAssetIds={staleAssetIds}
          addMode={addMode}
          onSelectAsset={(assetId) => {
            if (linkMode) {
              if (!pendingParentId) {
                setPendingParentId(assetId);
                return;
              }
              if (pendingParentId !== assetId) {
                createDependency.mutate({
                  parent_asset_id: pendingParentId,
                  child_asset_id: assetId,
                });
              }
              setPendingParentId(null);
              return;
            }
            setNewAssetDraft(null);
            setSelectedAssetId(assetId);
          }}
          onMoveAsset={(assetId, x, y) => {
            updateAsset.mutate({ assetId, body: { x, y } });
          }}
          onPlaceNewAsset={(x, y) => {
            setSelectedAssetId(null);
            setNewAssetDraft({ x, y, name: "", type: "" });
          }}
        />
        <div className="card map-toolbar">
          <button type="button" className="btn btn-sm" onClick={() => fitToAssets(true)}>
            Fit view
          </button>
          {canEdit && (
            <>
            <button
              type="button"
              className={`btn btn-sm ${addMode ? "btn-active" : ""}`}
              onClick={() => {
                setAddMode((current) => !current);
                setNewAssetDraft(null);
              }}
            >
              {addMode ? "Cancel placing asset" : "+ Add asset"}
            </button>
            <button
              type="button"
              className={`btn btn-sm ${linkMode ? "btn-active" : ""}`}
              onClick={() => {
                setLinkMode((current) => !current);
                setPendingParentId(null);
              }}
            >
              {linkMode ? "Cancel linking" : "Link dependency"}
            </button>
            </>
          )}
        </div>
        <MapLegend />
        {linkMode && (
          <p
            className="card"
            style={{
              position: "absolute",
              top: 64,
              left: 12,
              margin: 0,
              padding: "8px 12px",
              fontSize: 13,
              color: "var(--color-text)",
              boxShadow: "var(--shadow-md)",
            }}
          >
            {pendingParentId
              ? `Click the asset "${assetsById.get(pendingParentId)?.name ?? pendingParentId}" depends on...`
              : "Click the asset that depends on another..."}
          </p>
        )}
      </div>

      <div className="scrollbar-thin map-sidebar">
        <UpdatedAgo scoredAt={latestScoredAt(healthRows)} clock={freshnessClock} />
        {newAssetDraft && canEdit && (
          <NewAssetForm
            draft={newAssetDraft}
            isSaving={createAsset.isPending}
            onCancel={() => setNewAssetDraft(null)}
            onSubmit={(name, type) => {
              createAsset.mutate(
                {
                  name,
                  type,
                  x: newAssetDraft.x,
                  y: newAssetDraft.y,
                  ...(currentLevel === undefined ? {} : { floor_level: currentLevel }),
                },
                {
                  onSuccess: () => {
                    setNewAssetDraft(null);
                    setAddMode(false);
                  },
                }
              );
            }}
          />
        )}

        {selectedAsset && (
          <AssetPanel
            key={selectedAsset.id}
            facilityId={facilityId}
            row={selectedRow ?? buildHealthRows([selectedAsset], [])[0]}
            dependencies={allDependencies}
            assetsById={assetsById}
            currentLevel={currentLevel}
            onJump={goToAsset}
            onClose={() => setSelectedAssetId(null)}
            closeLabel="Back"
            titleLevel={2}
            history={riskHistory.byAsset.get(selectedAsset.id)}
            edit={
              canEdit
                ? {
                    isSaving: updateAsset.isPending,
                    isDeleting: deleteAsset.isPending,
                    isDeletingDependency: deleteDependency.isPending,
                    onUpdate: (updates) =>
                      updateAsset.mutate({ assetId: selectedAsset.id, body: updates }),
                    onDelete: () =>
                      deleteAsset.mutate(selectedAsset.id, {
                        onSuccess: () => setSelectedAssetId(null),
                      }),
                    onDeleteDependency: (dependencyId) => deleteDependency.mutate(dependencyId),
                  }
                : undefined
            }
          />
        )}

        {!newAssetDraft && !selectedAsset && (
          <AssetBrowser
            rows={healthRows}
            floors={floors}
            currentLevel={currentLevel}
            isLoading={assetsQuery.isPending}
            onPick={goToAsset}
          />
        )}
      </div>
    </div>
    </AppShell>
  );
}

const LEGEND: { label: string; color: string }[] = [
  { label: "Low risk", color: "#2e7d32" },
  { label: "Medium", color: "#ed6c02" },
  { label: "High", color: "#c62828" },
  { label: "Not scored", color: "#9e9e9e" },
];

function MapLegend(): React.JSX.Element {
  return (
    <div className="card map-legend" aria-label="Risk legend">
      <strong>Asset risk</strong>
      {LEGEND.map((item) => (
        <span key={item.label} className="legend-item">
          <span className="legend-dot" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function SidebarHeader({
  title,
  onClose,
}: {
  title: string;
  onClose: () => void;
}): React.JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
      <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
        &larr; Back
      </button>
      <h2 style={{ margin: 0, fontSize: 14 }}>{title}</h2>
      <span style={{ width: 58 }} />
    </div>
  );
}

function NewAssetForm({
  draft,
  isSaving,
  onCancel,
  onSubmit,
}: {
  draft: NewAssetDraft;
  isSaving: boolean;
  onCancel: () => void;
  onSubmit: (name: string, type: string) => void;
}): React.JSX.Element {
  const [name, setName] = useState("");
  const [type, setType] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (name.trim() && type.trim()) {
          onSubmit(name.trim(), type.trim());
        }
      }}
    >
      <SidebarHeader title="New asset" onClose={onCancel} />

      <p
        style={{
          fontSize: 12,
          background: "var(--color-surface-alt)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-sm)",
          padding: "6px 10px",
          marginBottom: 16,
        }}
      >
        Position: ({draft.x.toFixed(1)}, {draft.y.toFixed(1)})
      </p>

      <div className="field">
        <label className="field-label" htmlFor="new-asset-name">
          Name
        </label>
        <input
          id="new-asset-name"
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="new-asset-type">
          Type
        </label>
        <input
          id="new-asset-type"
          className="input"
          value={type}
          onChange={(event) => setType(event.target.value)}
          required
        />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 20 }}>
        <button type="submit" className="btn btn-primary" disabled={isSaving} style={{ flex: 1 }}>
          {isSaving ? "Creating..." : "Create"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
