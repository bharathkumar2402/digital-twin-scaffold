import { useEffect } from "react";

import type { AlertToastItem } from "../hooks/useAlertsSocket";

const AUTO_DISMISS_MS = 8000;

interface AlertToastProps {
  alerts: AlertToastItem[];
  onDismiss: (id: string) => void;
}

// Stacked top-right, over the map - the DoD (PHASE_PLAN.md Phase 3) is "a manually
// injected anomaly produces a browser alert in under 2 seconds", so this renders
// unconditionally as soon as useAlertsSocket's WebSocket message arrives, no polling.
export function AlertToast({ alerts, onDismiss }: AlertToastProps): React.JSX.Element {
  return (
    <div
      style={{
        position: "absolute",
        top: 12,
        right: 12,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 1000,
      }}
    >
      {alerts.map((alert) => (
        <AlertToastCard key={alert.id} alert={alert} onDismiss={() => onDismiss(alert.id)} />
      ))}
    </div>
  );
}

function AlertToastCard({
  alert,
  onDismiss,
}: {
  alert: AlertToastItem;
  onDismiss: () => void;
}): React.JSX.Element {
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      role="alert"
      className="card"
      style={{
        background: "var(--color-danger-soft)",
        borderColor: "var(--color-danger-border)",
        boxShadow: "var(--shadow-md)",
        padding: "10px 12px",
        minWidth: 240,
        animation: "toast-in 0.18s ease-out",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            aria-hidden
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: "var(--color-danger)",
              flexShrink: 0,
            }}
          />
          <strong style={{ fontSize: 13, color: "var(--color-danger)" }}>Anomaly detected</strong>
        </div>
        <button type="button" className="btn btn-ghost btn-icon" onClick={onDismiss} aria-label="Dismiss alert">
          &times;
        </button>
      </div>
      <p style={{ margin: "4px 0 0 16px", fontSize: 13, color: "var(--color-text)" }}>
        {alert.sensor_type}: <strong>{alert.value}</strong>
        {alert.z_score !== null && (
          <span style={{ color: "var(--color-text-muted)" }}> (z={alert.z_score.toFixed(1)})</span>
        )}
      </p>
    </div>
  );
}
