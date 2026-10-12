import { useEffect, useState } from "react";

import { useLive } from "../hooks/useLiveState";
import { formatUpdatedAgo, serverNowMs, type ServerClock } from "../lib/updatedAgo";

const TICK_MS = 30_000;

interface UpdatedAgoProps {
  // When the newest risk score was computed (server time); null = nothing has been scored.
  scoredAt: string | null;
  // The server's clock as last reported; null falls back to the browser's.
  clock?: ServerClock | null;
}

// "Updated 3 min ago" plus whether the screen is live. Live = the push connection is open, so a new
// score appears by itself; otherwise the screen refreshes once a minute and says so.
export function UpdatedAgo({ scoredAt, clock = null }: UpdatedAgoProps): React.JSX.Element {
  const { connected } = useLive();
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const scoredMs = scoredAt === null ? NaN : Date.parse(scoredAt);
  const text = Number.isNaN(scoredMs)
    ? "Not scored yet"
    : `Updated ${formatUpdatedAgo(serverNowMs(clock) - scoredMs)}`;

  return (
    <p className="updated-ago" role="status" aria-live="polite">
      <span className={`live-dot ${connected ? "live-on" : "live-off"}`} aria-hidden="true" />
      {text}
      <span className="updated-mode">
        {connected ? " \u00b7 Live" : " \u00b7 Reconnecting, refreshing every minute"}
      </span>
    </p>
  );
}
