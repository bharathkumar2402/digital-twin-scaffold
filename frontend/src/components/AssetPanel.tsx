import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { formatRul, type AssetHealthRow } from "../lib/assetHealth";
import { assetStatus } from "../lib/assetStatus";
import { connectionsOf, floorLabel, type Connection } from "../lib/floors";
import type { Asset, AssetDependency, AssetStatus, RiskHistoryPoint } from "../types/api";
import { OperatingStateBadge } from "./OperatingStateBadge";
import { RiskTrendChart } from "./RiskTrendChart";
import { StatusBadge } from "./StatusBadge";
import { TelemetryChart } from "./TelemetryChart";

// Manager-only controls. The host page passes this only for roles that may edit; without it the
// panel is read-only (the backend enforces the same rule).
export interface AssetPanelEdit {
  isSaving: boolean;
  isDeleting: boolean;
  isDeletingDependency: boolean;
  onUpdate: (updates: { status: AssetStatus }) => void;
  onDelete: () => void;
  onDeleteDependency: (dependencyId: string) => void;
}

interface AssetPanelProps {
  facilityId: string;
  row: AssetHealthRow;
  dependencies: AssetDependency[];
  assetsById: Map<string, Asset>;
  // Floor of the map being viewed; undefined when unknown (assets are then not tagged by floor).
  currentLevel?: number;
  onJump: (asset: Asset) => void;
  onClose: () => void;
  closeLabel?: string;
  // Where "Show on map" goes; omitted on the map itself.
  mapHref?: string | null;
  edit?: AssetPanelEdit;
  // Recent scores, oldest first. Optional: the panel is complete without them.
  history?: RiskHistoryPoint[];
  // The heading level of the asset's name: 3 under a page section's h2 (Assets), 2 straight under the
  // page's h1 (Map). Its sub-headings sit one level below it.
  titleLevel?: 2 | 3;
}

type HeadingTag = "h2" | "h3" | "h4";

function SectionHeading({
  children,
  tag: Tag,
}: {
  children: React.ReactNode;
  tag: HeadingTag;
}): React.JSX.Element {
  return <Tag className="panel-heading">{children}</Tag>;
}

function Fact({ label, value }: { label: string; value: React.ReactNode }): React.JSX.Element {
  return (
    <div className="panel-fact">
      <span style={{ color: "var(--color-text-muted)" }}>{label}</span>
      <span style={{ textAlign: "right", wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

function ConnectionList({
  items,
  currentLevel,
  onJump,
  edit,
}: {
  items: Connection[];
  currentLevel: number | undefined;
  onJump: (asset: Asset) => void;
  edit: AssetPanelEdit | undefined;
}): React.JSX.Element {
  if (items.length === 0) return <p style={{ fontSize: 13 }}>None</p>;
  return (
    <ul className="panel-list">
      {items.map((conn) => (
        <li key={conn.dependencyId} className="card panel-list-item">
          <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <button
              type="button"
              className="link-button"
              title={`Show ${conn.asset.name} on the map`}
              onClick={() => onJump(conn.asset)}
            >
              {conn.asset.name}
            </button>
            {currentLevel !== undefined && conn.asset.floor_level !== currentLevel && (
              <span className="floor-tag" title="On another floor - click the name to go there">
                {floorLabel(conn.asset.floor_level)}
              </span>
            )}
          </span>
          {edit && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={edit.isDeletingDependency}
              onClick={() => edit.onDeleteDependency(conn.dependencyId)}
            >
              Unlink
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

// The one place an asset's details are shown: used beside the Results table and in the map's
// sidebar, so both screens always tell the same story about the same asset.
export function AssetPanel({
  facilityId,
  row,
  dependencies,
  assetsById,
  currentLevel,
  onJump,
  onClose,
  closeLabel = "Close",
  mapHref = null,
  edit,
  history = [],
  titleLevel = 3,
}: AssetPanelProps): React.JSX.Element {
  const { asset } = row;
  const telemetry = useAssetTelemetry(facilityId, asset.id);
  const status = assetStatus(row);
  const vib = row.vibration;
  const { dependsOn, dependedOnBy } = connectionsOf(asset.id, dependencies, assetsById);
  const ref = useRef<HTMLElement>(null);
  const Title: HeadingTag = titleLevel === 2 ? "h2" : "h3";
  const sub: HeadingTag = titleLevel === 2 ? "h3" : "h4";

  // Move keyboard/screen-reader focus into the panel when it opens or switches asset.
  useEffect(() => {
    ref.current?.focus();
  }, [asset.id]);

  return (
    <aside ref={ref} tabIndex={-1} aria-label={`Details for ${asset.name}`} className="asset-panel">
      <div className="panel-top">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          {closeLabel}
        </button>
        {mapHref && (
          <Link className="btn btn-sm" to={mapHref}>
            Show on map
          </Link>
        )}
      </div>

      <Title style={{ fontSize: 17, marginBottom: 2 }}>{asset.name}</Title>
      <p style={{ fontSize: 12.5, margin: "0 0 10px" }}>
        {asset.type} &middot; {floorLabel(asset.floor_level)}
      </p>

      <p className="status-line">
        <StatusBadge status={status} /> {status.reasons.join("; ")}. <strong>{status.action}.</strong>
      </p>

      <SectionHeading tag={sub}>Health</SectionHeading>
      <Fact label="Operating state" value={<OperatingStateBadge state={asset.status} />} />
      <Fact
        label="Risk score"
        value={
          row.risk === null
            ? "not scored"
            : `${row.risk.toFixed(1)}${row.riskModelVersion ? ` (risk model ${row.riskModelVersion})` : ""}`
        }
      />
      <Fact
        label="Remaining life"
        value={
          row.rulCycles === null
            ? "—"
            : `${formatRul(row.rulCycles)} cycles (${row.rulModelVersion ?? "unknown model"}, ${row.rulDatasetName ?? "unknown dataset"})`
        }
      />
      <Fact
        label="Vibration"
        value={
          vib === null
            ? (row.vibrationUnavailableReason ?? "no data")
            : `${vib.alarm ? "ALARM" : "normal"} – peak score ${vib.maxScore?.toFixed(2) ?? "?"} vs threshold ${vib.threshold?.toFixed(2) ?? "?"} (${vib.modelVersion ?? "unknown model"}, ${vib.datasetName ?? "unknown dataset"})`
        }
      />
      {row.scoredAt && <Fact label="Scored at" value={new Date(row.scoredAt).toLocaleString()} />}

      {history.length > 0 && (
        <>
          <SectionHeading tag={sub}>Risk trend</SectionHeading>
          <RiskTrendChart points={history} />
        </>
      )}

      <SectionHeading tag={sub}>Details</SectionHeading>
      <Fact label="Position" value={`(${asset.x.toFixed(1)}, ${asset.y.toFixed(1)})`} />
      {asset.manufacturer && <Fact label="Manufacturer" value={asset.manufacturer} />}
      {asset.model && <Fact label="Model" value={asset.model} />}
      {asset.installed_date && <Fact label="Installed" value={asset.installed_date} />}

      {edit && (
        <div className="field" style={{ marginTop: 10 }}>
          <label className="field-label" htmlFor="asset-operating-state">
            Change operating state
          </label>
          <select
            id="asset-operating-state"
            className="select"
            value={asset.status}
            disabled={edit.isSaving}
            onChange={(event) => edit.onUpdate({ status: event.target.value as AssetStatus })}
          >
            <option value="operational">Operational</option>
            <option value="maintenance">Maintenance</option>
            <option value="offline">Offline</option>
          </select>
        </div>
      )}

      <SectionHeading tag={sub}>Telemetry</SectionHeading>
      {telemetry.isPending && <p style={{ fontSize: 13 }}>Loading telemetry...</p>}
      {telemetry.isError && (
        <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
          Could not load telemetry.
        </p>
      )}
      {telemetry.data && (
        <div className="card" style={{ padding: 8 }}>
          <TelemetryChart readings={telemetry.data} />
        </div>
      )}

      <SectionHeading tag={sub}>Depends on</SectionHeading>
      <ConnectionList items={dependsOn} currentLevel={currentLevel} onJump={onJump} edit={edit} />

      <SectionHeading tag={sub}>Depended on by</SectionHeading>
      <ConnectionList items={dependedOnBy} currentLevel={currentLevel} onJump={onJump} edit={edit} />

      {edit && (
        <button
          type="button"
          className="btn btn-danger"
          onClick={edit.onDelete}
          disabled={edit.isDeleting}
          style={{ width: "100%", marginTop: 20 }}
        >
          {edit.isDeleting ? "Deleting..." : "Delete asset"}
        </button>
      )}
    </aside>
  );
}
