import type maplibregl from "maplibre-gl";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { AlertToast } from "../components/AlertToast";
import { AssetLayer } from "../components/AssetLayer";
import { DependencyLayer } from "../components/DependencyLayer";
import { FacilityMap } from "../components/FacilityMap";
import { TelemetryChart } from "../components/TelemetryChart";
import {
  useAssetDependencies,
  useCreateAssetDependency,
  useDeleteAssetDependency,
} from "../hooks/useAssetDependencies";
import { useAlertsSocket } from "../hooks/useAlertsSocket";
import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { useAuth } from "../hooks/useAuth";
import { useAssets, useCreateAsset, useDeleteAsset, useUpdateAsset } from "../hooks/useAssets";
import { useFacilityMapUpload } from "../hooks/useFacilityMapUpload";
import { useRiskScores } from "../hooks/useRiskScores";
import type { Asset, AssetDependency, AssetStatus } from "../types/api";

// Mirrors backend/app/core/rbac.py's WRITE_ROLES in app/api/assets.py - technician and
// viewer stay read-only on the map, same restriction as facility map uploads.
const WRITE_ROLES = new Set(["superadmin", "tenant_admin", "facility_manager"]);

const SIDEBAR_WIDTH = 320;

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
  const { alerts, dismiss } = useAlertsSocket();

  const [map, setMap] = useState<maplibregl.Map | null>(null);
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

  const assets = useMemo(() => assetsQuery.data ?? [], [assetsQuery.data]);
  const dependencies = useMemo(() => dependenciesQuery.data ?? [], [dependenciesQuery.data]);
  const selectedAsset = assets.find((asset) => asset.id === selectedAssetId) ?? null;
  const assetsById = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);
  // Highest-computed_at score per asset - get_latest_risk_scores already returns at
  // most one row per asset, but the map lookup itself doesn't assume that ordering.
  const riskScoresByAssetId = useMemo(() => {
    const scores = riskScoresQuery.data ?? [];
    return new Map(scores.map((riskScore) => [riskScore.asset_id, riskScore.score]));
  }, [riskScoresQuery.data]);

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
    <div style={{ width: "100vw", height: "100vh", display: "flex", background: "var(--color-bg)" }}>
      <div style={{ flex: 1, position: "relative" }}>
        <AlertToast alerts={alerts} onDismiss={dismiss} />
        <FacilityMap tileUrlTemplate={data.tile_url_template} onMapLoad={setMap} />
        <DependencyLayer map={map} assets={assets} dependencies={dependencies} />
        <AssetLayer
          map={map}
          assets={assets}
          riskScores={riskScoresByAssetId}
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
        {canEdit && (
          <div
            className="card"
            style={{
              position: "absolute",
              top: 12,
              left: 12,
              display: "flex",
              gap: 6,
              padding: 6,
              boxShadow: "var(--shadow-md)",
            }}
          >
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
          </div>
        )}
        {linkMode && (
          <p
            className="card"
            style={{
              position: "absolute",
              top: canEdit ? 56 : 12,
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

      <div
        className="scrollbar-thin"
        style={{
          width: SIDEBAR_WIDTH,
          flexShrink: 0,
          padding: 16,
          overflowY: "auto",
          borderLeft: "1px solid var(--color-border)",
          background: "var(--color-surface)",
        }}
      >
        {newAssetDraft && canEdit && (
          <NewAssetForm
            draft={newAssetDraft}
            isSaving={createAsset.isPending}
            onCancel={() => setNewAssetDraft(null)}
            onSubmit={(name, type) => {
              createAsset.mutate(
                { name, type, x: newAssetDraft.x, y: newAssetDraft.y },
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
          <AssetDetailPanel
            key={selectedAsset.id}
            facilityId={facilityId}
            asset={selectedAsset}
            canEdit={canEdit}
            isSaving={updateAsset.isPending}
            isDeleting={deleteAsset.isPending}
            dependencies={dependencies}
            assetsById={assetsById}
            isDeletingDependency={deleteDependency.isPending}
            onUpdate={(updates) => updateAsset.mutate({ assetId: selectedAsset.id, body: updates })}
            onDelete={() => {
              deleteAsset.mutate(selectedAsset.id, {
                onSuccess: () => setSelectedAssetId(null),
              });
            }}
            onDeleteDependency={(dependencyId) => deleteDependency.mutate(dependencyId)}
            onClose={() => setSelectedAssetId(null)}
          />
        )}

        {!newAssetDraft && !selectedAsset && (
          <>
            <div style={{ marginBottom: 14 }}>
              <h3 style={{ fontSize: 15 }}>Assets</h3>
              <p style={{ fontSize: 12.5, margin: 0 }}>{assets.length} on this facility</p>
            </div>
            {assetsQuery.isPending && <p style={{ fontSize: 13 }}>Loading assets...</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              {assets.map((asset) => (
                <li key={asset.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedAssetId(asset.id)}
                    className="card"
                    style={{
                      width: "100%",
                      textAlign: "left",
                      padding: "10px 12px",
                      cursor: "pointer",
                      background: "var(--color-surface-alt)",
                      border: "1px solid var(--color-border)",
                    }}
                  >
                    <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--color-text)" }}>
                      {asset.name}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 3 }}>
                      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>{asset.type}</span>
                      <StatusBadge status={asset.status} />
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: AssetStatus }): React.JSX.Element {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {status}
    </span>
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
      <h3 style={{ margin: 0, fontSize: 14 }}>{title}</h3>
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

function AssetDetailPanel({
  facilityId,
  asset,
  canEdit,
  isSaving,
  isDeleting,
  dependencies,
  assetsById,
  isDeletingDependency,
  onUpdate,
  onDelete,
  onDeleteDependency,
  onClose,
}: {
  facilityId: string;
  asset: Asset;
  canEdit: boolean;
  isSaving: boolean;
  isDeleting: boolean;
  dependencies: AssetDependency[];
  assetsById: Map<string, Asset>;
  isDeletingDependency: boolean;
  onUpdate: (updates: { name?: string; type?: string; status?: AssetStatus }) => void;
  onDelete: () => void;
  onDeleteDependency: (dependencyId: string) => void;
  onClose: () => void;
}): React.JSX.Element {
  const telemetryQuery = useAssetTelemetry(facilityId, asset.id);
  // parent depends_on child (see AssetDependency's docstring in types/api.ts) - so
  // "depends on" (upstream) is where this asset is the parent, "depended on by"
  // (downstream) is where it's the child.
  const dependsOn = dependencies.filter((dep) => dep.parent_asset_id === asset.id);
  const dependedOnBy = dependencies.filter((dep) => dep.child_asset_id === asset.id);

  return (
    <div>
      <SidebarHeader title="Asset detail" onClose={onClose} />

      <div style={{ marginBottom: 18 }}>
        <h3 style={{ fontSize: 17 }}>{asset.name}</h3>
        <p style={{ fontSize: 12.5, margin: "2px 0 10px" }}>{asset.type}</p>
        <StatusBadge status={asset.status} />
      </div>

      <dl className="card" style={{ padding: "10px 12px", margin: "0 0 18px", background: "var(--color-surface-alt)" }}>
        <DetailRow label="Position">
          ({asset.x.toFixed(1)}, {asset.y.toFixed(1)})
        </DetailRow>
        {asset.manufacturer && <DetailRow label="Manufacturer">{asset.manufacturer}</DetailRow>}
        {asset.model && <DetailRow label="Model">{asset.model}</DetailRow>}
        {asset.installed_date && <DetailRow label="Installed">{asset.installed_date}</DetailRow>}
      </dl>

      <div className="field">
        <label className="field-label" htmlFor="asset-status">
          Status
        </label>
        <select
          id="asset-status"
          className="select"
          value={asset.status}
          disabled={!canEdit || isSaving}
          onChange={(event) => onUpdate({ status: event.target.value as AssetStatus })}
        >
          <option value="operational">Operational</option>
          <option value="maintenance">Maintenance</option>
          <option value="offline">Offline</option>
        </select>
      </div>

      <SectionHeading>Telemetry</SectionHeading>
      {telemetryQuery.isPending && <p style={{ fontSize: 13 }}>Loading telemetry...</p>}
      {telemetryQuery.isError && (
        <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
          Failed to load telemetry: {telemetryQuery.error.message}
        </p>
      )}
      {telemetryQuery.isSuccess && (
        <div className="card" style={{ padding: 8, marginBottom: 20 }}>
          <TelemetryChart readings={telemetryQuery.data} />
        </div>
      )}

      <SectionHeading>Maintenance history</SectionHeading>
      <p style={{ fontSize: 13 }}>No maintenance records yet - scheduling arrives in a later phase.</p>

      <SectionHeading>Depends on</SectionHeading>
      <DependencyList
        items={dependsOn}
        resolveName={(dep) => assetsById.get(dep.child_asset_id)?.name ?? dep.child_asset_id}
        canEdit={canEdit}
        isDeleting={isDeletingDependency}
        onDelete={onDeleteDependency}
      />

      <SectionHeading>Depended on by</SectionHeading>
      <DependencyList
        items={dependedOnBy}
        resolveName={(dep) => assetsById.get(dep.parent_asset_id)?.name ?? dep.parent_asset_id}
        canEdit={canEdit}
        isDeleting={isDeletingDependency}
        onDelete={onDeleteDependency}
      />

      {canEdit && (
        <button
          type="button"
          className="btn btn-danger"
          onClick={onDelete}
          disabled={isDeleting}
          style={{ width: "100%", marginTop: 20 }}
        >
          {isDeleting ? "Deleting..." : "Delete asset"}
        </button>
      )}
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <h4
      style={{
        fontSize: 11,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color: "var(--color-text-subtle)",
        margin: "20px 0 8px",
      }}
    >
      {children}
    </h4>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "3px 0", fontSize: 12.5 }}>
      <dt style={{ color: "var(--color-text-muted)" }}>{label}</dt>
      <dd style={{ margin: 0, color: "var(--color-text)", fontWeight: 500, textAlign: "right" }}>{children}</dd>
    </div>
  );
}

function DependencyList({
  items,
  resolveName,
  canEdit,
  isDeleting,
  onDelete,
}: {
  items: AssetDependency[];
  resolveName: (dep: AssetDependency) => string;
  canEdit: boolean;
  isDeleting: boolean;
  onDelete: (dependencyId: string) => void;
}): React.JSX.Element {
  if (items.length === 0) {
    return <p style={{ fontSize: 13 }}>None</p>;
  }
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: "0 0 4px", display: "flex", flexDirection: "column", gap: 6 }}>
      {items.map((dep) => (
        <li
          key={dep.id}
          className="card"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "6px 10px",
            fontSize: 13,
            background: "var(--color-surface-alt)",
          }}
        >
          <span>{resolveName(dep)}</span>
          {canEdit && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={isDeleting}
              onClick={() => onDelete(dep.id)}
            >
              Unlink
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
