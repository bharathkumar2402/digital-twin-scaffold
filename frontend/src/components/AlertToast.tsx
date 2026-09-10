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
        top: 8,
        right: 8,
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
      style={{
        background: "#fdecea",
        border: "1px solid #f5c6cb",
        borderRadius: 4,
        padding: "8px 12px",
        minWidth: 220,
        boxShadow: "0 1px 4px rgba(0,0,0,0.2)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <strong>Anomaly detected</strong>
        <button type="button" onClick={onDismiss} aria-label="Dismiss alert">
          &times;
        </button>
      </div>
      <p style={{ margin: "4px 0 0" }}>
        {alert.sensor_type}: {alert.value}
        {alert.z_score !== null && ` (z=${alert.z_score.toFixed(1)})`}
      </p>
    </div>
  );
}
