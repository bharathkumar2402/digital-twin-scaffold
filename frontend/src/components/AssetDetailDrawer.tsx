import { useEffect, useRef } from "react";

import { useAssetTelemetry } from "../hooks/useAssetTelemetry";
import { formatRul, type AssetHealthRow } from "../lib/assetHealth";
import { TelemetryChart } from "./TelemetryChart";

interface AssetDetailDrawerProps {
  facilityId: string;
  row: AssetHealthRow;
  onClose: () => void;
}

function Fact({ label, value }: { label: string; value: React.ReactNode }): React.JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 13, padding: "3px 0" }}>
      <span style={{ color: "var(--color-text-muted)" }}>{label}</span>
      <span style={{ textAlign: "right", wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

export function AssetDetailDrawer({
  facilityId,
  row,
  onClose,
}: AssetDetailDrawerProps): React.JSX.Element {
  const telemetry = useAssetTelemetry(facilityId, row.asset.id);
  const vib = row.vibration;
  const ref = useRef<HTMLElement>(null);

  // Move keyboard/screen-reader focus into the drawer when it opens or switches asset.
  useEffect(() => {
    ref.current?.focus();
  }, [row.asset.id]);

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      aria-label={`Details for ${row.asset.name}`}
      className="stage-card"
      style={{ marginTop: 12 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h4 style={{ margin: 0 }}>{row.asset.name}</h4>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      </div>

      <Fact label="Status" value={row.asset.status} />
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

      <h4 style={{ marginTop: 14 }}>Telemetry</h4>
      {telemetry.isPending && <p style={{ fontSize: 13 }}>Loading telemetry...</p>}
      {telemetry.isError && (
        <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
          Could not load telemetry.
        </p>
      )}
      {telemetry.data && <TelemetryChart readings={telemetry.data} />}
    </aside>
  );
}
