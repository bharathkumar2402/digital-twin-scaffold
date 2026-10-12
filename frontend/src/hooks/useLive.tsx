import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { getAccessToken } from "../lib/apiClient";
import { parseLiveFrame } from "../lib/liveMessages";
import { useAuth } from "./useAuth";
import {
  INVALIDATE_DEBOUNCE_MS,
  LIVE_QUERY_KEYS,
  LiveContext,
  RECONNECT_DELAY_MS,
  type AlertToastItem,
} from "./useLiveState";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// The browser's WebSocket API cannot set headers on the handshake (see backend/app/api/alerts_ws.py),
// so the access token rides as a query param instead of the Authorization header.
function liveSocketUrl(): string {
  const base = new URL(API_BASE_URL);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  base.pathname = "/ws/alerts";
  const token = getAccessToken();
  if (token) base.searchParams.set("token", token);
  return base.toString();
}

export function LiveProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [alerts, setAlerts] = useState<AlertToastItem[]>([]);
  // Facilities to refresh at the next flush; null = every facility (after a reconnect).
  const pending = useRef<Set<string> | null | "empty">("empty");
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dismiss = useCallback((id: string) => {
    setAlerts((current) => current.filter((alert) => alert.id !== id));
  }, []);

  useEffect(() => {
    if (!isAuthenticated) return undefined;

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let hasConnectedBefore = false;

    function flush(): void {
      flushTimer.current = null;
      const what = pending.current;
      pending.current = "empty";
      if (what === "empty") return;
      for (const key of LIVE_QUERY_KEYS) {
        if (what === null) {
          void queryClient.invalidateQueries({ queryKey: [key] });
        } else {
          for (const facilityId of what) {
            void queryClient.invalidateQueries({ queryKey: [key, facilityId] });
          }
        }
      }
    }

    function scheduleRefresh(facilityId: string | null): void {
      if (facilityId === null) pending.current = null;
      else if (pending.current === "empty") pending.current = new Set([facilityId]);
      else if (pending.current !== null) pending.current.add(facilityId);
      if (flushTimer.current === null) {
        flushTimer.current = setTimeout(flush, INVALIDATE_DEBOUNCE_MS);
      }
    }

    function connect(): void {
      if (cancelled || !getAccessToken()) return;
      const ws = new WebSocket(liveSocketUrl());
      socket = ws;

      ws.onopen = () => {
        if (cancelled || socket !== ws) return;
        setConnected(true);
        // Pushes sent while the socket was down were lost: catch up once on reconnect.
        if (hasConnectedBefore) scheduleRefresh(null);
        hasConnectedBefore = true;
      };

      ws.onmessage = (event: MessageEvent<string>) => {
        if (cancelled || socket !== ws) return;
        const message = parseLiveFrame(event.data);
        if (message === null) return; // malformed or unknown: dropped, the socket carries on
        if (message.kind === "scores_updated") {
          scheduleRefresh(message.facilityId);
        } else {
          const { alert } = message;
          setAlerts((current) => [
            ...current,
            { ...alert, id: `${alert.asset_id}-${alert.triggered_at}` },
          ]);
        }
      };

      ws.onclose = () => {
        if (socket !== ws) return;
        socket = null;
        if (!cancelled) {
          setConnected(false);
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (flushTimer.current) clearTimeout(flushTimer.current);
      flushTimer.current = null;
      pending.current = "empty";
      const closing = socket;
      socket = null;
      closing?.close();
      setConnected(false);
    };
  }, [isAuthenticated, queryClient]);

  const value = useMemo(() => ({ connected, alerts, dismiss }), [connected, alerts, dismiss]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}
