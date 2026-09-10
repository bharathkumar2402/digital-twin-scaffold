import { useCallback, useEffect, useState } from "react";

import { getAccessToken } from "../lib/apiClient";
import type { AlertNotification } from "../types/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Backend has no way to set custom WebSocket headers on the handshake (see
// backend/app/api/alerts_ws.py's docstring), so the access token rides as a query
// param instead of the `Authorization` header the rest of the app uses.
function alertsSocketUrl(): string {
  const base = new URL(API_BASE_URL);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  base.pathname = "/ws/alerts";
  const token = getAccessToken();
  if (token) {
    base.searchParams.set("token", token);
  }
  return base.toString();
}

const RECONNECT_DELAY_MS = 2000;

export interface AlertToastItem extends AlertNotification {
  id: string;
}

export interface UseAlertsSocketResult {
  alerts: AlertToastItem[];
  dismiss: (id: string) => void;
}

// Reconnects on drop (e.g. the access token was rotated, or a transient network blip)
// as long as the component using this hook stays mounted - the alert toast/map view
// should feel "always live", not something the user has to refresh to re-arm.
export function useAlertsSocket(): UseAlertsSocketResult {
  const [alerts, setAlerts] = useState<AlertToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setAlerts((current) => current.filter((alert) => alert.id !== id));
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    function connect(): void {
      if (cancelled || !getAccessToken()) {
        return;
      }
      socket = new WebSocket(alertsSocketUrl());

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const notification = JSON.parse(event.data) as AlertNotification;
          setAlerts((current) => [
            ...current,
            { ...notification, id: `${notification.asset_id}-${notification.triggered_at}` },
          ]);
        } catch {
          // Malformed frame - drop it rather than crash the socket handler.
        }
      };

      socket.onclose = () => {
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, []);

  return { alerts, dismiss };
}
