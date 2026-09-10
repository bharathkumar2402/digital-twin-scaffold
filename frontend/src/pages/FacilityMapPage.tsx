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
import type { Asset, AssetDependency, AssetStatus } from "../types/api";

// Mirrors backend/app/core/rbac.py's WRITE_ROLES in app/api/assets.py - technician and
// viewer stay read-only on the map, same restriction as facility map uploads.
const WRITE_ROLES = new Set(["superadmin", "tenant_admin", "facility_manager"]);

interface NewAssetDraft {
  x: number;
  y: number;
  name: string;
  type: string;
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

  const assets = useMemo(() => assetsQuery.data ?? [], [assetsQuery.data]);
  const dependencies = useMemo(() => dependenciesQuery.data ?? [], [dependenciesQuery.data]);
  const selectedAsset = assets.find((asset) => asset.id === selectedAssetId) ?? null;
  const assetsById = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);

  if (!facilityId || !uploadId) {
    return <p>Missing facility or upload id in the URL.</p>;
  }
  if (isPending) {
    return <p>Loading upload status...</p>;
  }
  if (isError) {
    return <p role="alert">Failed to load upload status: {error.message}</p>;
  }
  if (data.status === "failed" || data.status === "conversion_failed") {
    return <p role="alert">Floor plan processing failed: {data.status}</p>;
  }
  if (data.status !== "tiled" || !data.tile_url_template) {
    return <p>Processing floor plan ({data.status})...</p>;
  }

  return (
    <div style={{ width: "100vw", height: "100vh", display: "flex" }}>
      <div style={{ flex: 1, position: "relative" }}>
        <AlertToast alerts={alerts} onDismiss={dismiss} />
        <FacilityMap tileUrlTemplate={data.tile_url_template} onMapLoad={setMap} />
        <DependencyLayer map={map} assets={assets} dependencies={dependencies} />
        <AssetLayer
          map={map}
          assets={assets}
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
          <div style={{ position: "absolute", top: 8, left: 8, display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => {
                setAddMode((current) => !current);
                setNewAssetDraft(null);
              }}
            >
              {addMode ? "Cancel placing asset" : "Add asset"}
            </button>
            <button
              type="button"
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
          <p style={{ position: "absolute", top: 40, left: 8, background: "#fff", padding: 4 }}>
            {pendingParentId
              ? `Click the asset "${assetsById.get(pendingParentId)?.name ?? pendingParentId}" depends on...`
              : "Click the asset that depends on another..."}
          </p>
        )}
      </div>

      <div style={{ width: 280, padding: 12, overflowY: "auto", borderLeft: "1px solid #ccc" }}>
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
            <h3>Assets</h3>
            {assetsQuery.isPending && <p>Loading assets...</p>}
            <ul style={{ listStyle: "none", padding: 0 }}>
              {assets.map((asset) => (
                <li key={asset.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedAssetId(asset.id)}
                    style={{ width: "100%", textAlign: "left" }}
                  >
                    {asset.name} ({asset.type})
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
      <h3>New asset</h3>
      <p>
        Position: ({draft.x.toFixed(1)}, {draft.y.toFixed(1)})
      </p>
      <label>
        Name
        <input value={name} onChange={(event) => setName(event.target.value)} required />
      </label>
      <br />
      <label>
        Type
        <input value={type} onChange={(event) => setType(event.target.value)} required />
      </label>
      <br />
      <button type="submit" disabled={isSaving}>
        Create
      </button>
      <button type="button" onClick={onCancel}>
        Cancel
      </button>
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
      <button type="button" onClick={onClose}>
        &larr; Back
      </button>
      <h3>{asset.name}</h3>
      <p>Type: {asset.type}</p>
      <p>
        Position: ({asset.x.toFixed(1)}, {asset.y.toFixed(1)})
      </p>
      <label>
        Status
        <select
          value={asset.status}
          disabled={!canEdit || isSaving}
          onChange={(event) => onUpdate({ status: event.target.value as AssetStatus })}
        >
          <option value="operational">Operational</option>
          <option value="maintenance">Maintenance</option>
          <option value="offline">Offline</option>
        </select>
      </label>
      {asset.manufacturer && <p>Manufacturer: {asset.manufacturer}</p>}
      {asset.model && <p>Model: {asset.model}</p>}
      {asset.installed_date && <p>Installed: {asset.installed_date}</p>}

      <h4>Telemetry</h4>
      {telemetryQuery.isPending && <p>Loading telemetry...</p>}
      {telemetryQuery.isError && (
        <p role="alert">Failed to load telemetry: {telemetryQuery.error.message}</p>
      )}
      {telemetryQuery.isSuccess && <TelemetryChart readings={telemetryQuery.data} />}

      <h4>Maintenance history</h4>
      <p>No maintenance records yet - scheduling arrives in a later phase.</p>

      <h4>Depends on</h4>
      {dependsOn.length === 0 && <p>None</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {dependsOn.map((dep) => (
          <li key={dep.id}>
            {assetsById.get(dep.child_asset_id)?.name ?? dep.child_asset_id}
            {canEdit && (
              <button
                type="button"
                disabled={isDeletingDependency}
                onClick={() => onDeleteDependency(dep.id)}
              >
                Unlink
              </button>
            )}
          </li>
        ))}
      </ul>

      <h4>Depended on by</h4>
      {dependedOnBy.length === 0 && <p>None</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {dependedOnBy.map((dep) => (
          <li key={dep.id}>
            {assetsById.get(dep.parent_asset_id)?.name ?? dep.parent_asset_id}
            {canEdit && (
              <button
                type="button"
                disabled={isDeletingDependency}
                onClick={() => onDeleteDependency(dep.id)}
              >
                Unlink
              </button>
            )}
          </li>
        ))}
      </ul>

      {canEdit && (
        <button type="button" onClick={onDelete} disabled={isDeleting}>
          Delete asset
        </button>
      )}
    </div>
  );
}
