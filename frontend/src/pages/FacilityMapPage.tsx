import type maplibregl from "maplibre-gl";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { AssetLayer } from "../components/AssetLayer";
import { FacilityMap } from "../components/FacilityMap";
import { useAuth } from "../hooks/useAuth";
import { useAssets, useCreateAsset, useDeleteAsset, useUpdateAsset } from "../hooks/useAssets";
import { useFacilityMapUpload } from "../hooks/useFacilityMapUpload";
import type { Asset, AssetStatus } from "../types/api";

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

  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const [addMode, setAddMode] = useState(false);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [newAssetDraft, setNewAssetDraft] = useState<NewAssetDraft | null>(null);

  const assetsQuery = useAssets(facilityId ?? "");
  const createAsset = useCreateAsset(facilityId ?? "");
  const updateAsset = useUpdateAsset(facilityId ?? "");
  const deleteAsset = useDeleteAsset(facilityId ?? "");

  const assets = useMemo(() => assetsQuery.data ?? [], [assetsQuery.data]);
  const selectedAsset = assets.find((asset) => asset.id === selectedAssetId) ?? null;

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
        <FacilityMap tileUrlTemplate={data.tile_url_template} onMapLoad={setMap} />
        <AssetLayer
          map={map}
          assets={assets}
          addMode={addMode}
          onSelectAsset={(assetId) => {
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
          <button
            type="button"
            style={{ position: "absolute", top: 8, left: 8 }}
            onClick={() => {
              setAddMode((current) => !current);
              setNewAssetDraft(null);
            }}
          >
            {addMode ? "Cancel placing asset" : "Add asset"}
          </button>
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
            asset={selectedAsset}
            canEdit={canEdit}
            isSaving={updateAsset.isPending}
            isDeleting={deleteAsset.isPending}
            onUpdate={(updates) => updateAsset.mutate({ assetId: selectedAsset.id, body: updates })}
            onDelete={() => {
              deleteAsset.mutate(selectedAsset.id, {
                onSuccess: () => setSelectedAssetId(null),
              });
            }}
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
  asset,
  canEdit,
  isSaving,
  isDeleting,
  onUpdate,
  onDelete,
  onClose,
}: {
  asset: Asset;
  canEdit: boolean;
  isSaving: boolean;
  isDeleting: boolean;
  onUpdate: (updates: { name?: string; type?: string; status?: AssetStatus }) => void;
  onDelete: () => void;
  onClose: () => void;
}): React.JSX.Element {
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
      {canEdit && (
        <button type="button" onClick={onDelete} disabled={isDeleting}>
          Delete asset
        </button>
      )}
    </div>
  );
}
