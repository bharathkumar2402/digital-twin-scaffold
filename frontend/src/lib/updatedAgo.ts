// "Updated 3 min ago": how long since the newest risk score was computed, in the units a person uses.

export function formatUpdatedAgo(ageMs: number): string {
  // A score stamped slightly ahead of the clock we compare with is "just now", never negative.
  const seconds = Math.max(0, Math.floor(ageMs / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

// The browser's clock can be wrong, so ages are measured on the server's: `asOf` is the server time
// the freshness lookup reported and `fetchedAtMs` is the browser time that answer arrived. With no
// such answer yet it falls back to the browser clock.
export interface ServerClock {
  asOfMs: number;
  fetchedAtMs: number;
}

export function serverNowMs(clock: ServerClock | null, browserNowMs: number = Date.now()): number {
  if (clock === null || !Number.isFinite(clock.asOfMs) || !Number.isFinite(clock.fetchedAtMs)) {
    return browserNowMs;
  }
  return clock.asOfMs + Math.max(0, browserNowMs - clock.fetchedAtMs);
}
