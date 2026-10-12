import { createContext, useContext } from "react";

import type { AlertNotification } from "../types/api";

// Shared by the provider (useLive.tsx) and by anything that reads the live state.
export const RECONNECT_DELAY_MS = 2000;
// Frames that arrive together (a burst, or a reconnect) cause ONE refetch, this long after the last.
export const INVALIDATE_DEBOUNCE_MS = 500;
// While the socket is down the screens poll at this pace instead of waiting for a push.
export const FALLBACK_POLL_MS = 60_000;

// Query keys a `scores_updated` push makes stale. `telemetry-freshness` is included although it also
// polls on its own: silence produces no event, so polling stays the only way to notice a quiet sensor.
export const LIVE_QUERY_KEYS = ["risk-scores", "risk-history", "telemetry-freshness"] as const;

export interface AlertToastItem extends AlertNotification {
  id: string;
}

export interface LiveState {
  // True while the push connection is open. Screens use it for the "Live" label and to stop polling.
  connected: boolean;
  alerts: AlertToastItem[];
  dismiss: (id: string) => void;
}

// Without a provider (tests, a page rendered alone) there is no socket: not connected, so polling is on.
export const NO_LIVE: LiveState = { connected: false, alerts: [], dismiss: () => undefined };
export const LiveContext = createContext<LiveState>(NO_LIVE);

export function useLive(): LiveState {
  return useContext(LiveContext);
}

// The polling interval for data that a push normally refreshes: off while connected, 60 s otherwise.
export function useFallbackInterval(): number | false {
  return useLive().connected ? false : FALLBACK_POLL_MS;
}
