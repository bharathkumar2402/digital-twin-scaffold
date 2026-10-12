import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { setAccessToken } from "../lib/apiClient";
import { useAuth } from "./useAuth";
import { LiveProvider } from "./useLive";
import {
  FALLBACK_POLL_MS,
  INVALIDATE_DEBOUNCE_MS,
  RECONNECT_DELAY_MS,
  useFallbackInterval,
  useLive,
} from "./useLiveState";

vi.mock("./useAuth", () => ({ useAuth: vi.fn() }));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.();
  }

  open(): void {
    this.onopen?.();
  }

  emit(data: unknown): void {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) } as MessageEvent<string>);
  }

  // The server or network drops the connection.
  drop(): void {
    this.onclose?.();
  }
}

const sockets = () => FakeWebSocket.instances;
const latest = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1]!;

const updated = (facility: string) => ({
  type: "scores_updated",
  facility_id: facility,
  scored: 44,
  computed_at: "2026-10-12T12:00:00Z",
});
const anomaly = {
  asset_id: "asset-1",
  sensor_type: "vibration",
  value: 999,
  z_score: 12.3,
  triggered_at: "2026-10-12T12:00:00Z",
};

let queryClient: QueryClient;
let invalidate: MockInstance<QueryClient["invalidateQueries"]>;

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <LiveProvider>{children}</LiveProvider>
    </QueryClientProvider>
  );
}

function setAuthenticated(isAuthenticated: boolean) {
  vi.mocked(useAuth).mockReturnValue({ isAuthenticated } as unknown as ReturnType<typeof useAuth>);
}

const invalidatedKeys = () =>
  invalidate.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown[] }).queryKey));

describe("LiveProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    setAccessToken("test-access-token");
    setAuthenticated(true);
    queryClient = new QueryClient();
    invalidate = vi.spyOn(queryClient, "invalidateQueries");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("opens one socket with the token, and is only 'connected' once it is open", () => {
    const { result } = renderHook(() => useLive(), { wrapper });
    expect(sockets()).toHaveLength(1);
    expect(latest().url).toContain("/ws/alerts");
    expect(latest().url).toContain("token=test-access-token");
    expect(result.current.connected).toBe(false);

    act(() => latest().open());
    expect(result.current.connected).toBe(true);
  });

  it("opens nothing before sign-in, and closes the socket on sign-out", () => {
    setAuthenticated(false);
    const { rerender } = renderHook(() => useLive(), { wrapper });
    expect(sockets()).toHaveLength(0);

    setAuthenticated(true);
    rerender();
    expect(sockets()).toHaveLength(1);
    const first = latest();

    setAuthenticated(false);
    rerender();
    expect(first.closed).toBe(true);
  });

  it("does not connect without an access token", () => {
    setAccessToken(null);
    renderHook(() => useLive(), { wrapper });
    expect(sockets()).toHaveLength(0);
  });

  it("shows an anomaly alert and lets it be dismissed", () => {
    const { result } = renderHook(() => useLive(), { wrapper });
    act(() => {
      latest().open();
      latest().emit(anomaly);
    });
    expect(result.current.alerts).toHaveLength(1);
    expect(result.current.alerts[0]?.asset_id).toBe("asset-1");

    act(() => result.current.dismiss(result.current.alerts[0]!.id));
    expect(result.current.alerts).toHaveLength(0);
  });

  it("a scores_updated frame refreshes that facility's scores, history and freshness, and nothing else", () => {
    const { result } = renderHook(() => useLive(), { wrapper });
    act(() => {
      latest().open();
      latest().emit(updated("f1"));
    });
    expect(invalidate).not.toHaveBeenCalled(); // debounced, not immediate
    expect(result.current.alerts).toHaveLength(0); // and never an alert toast

    act(() => {
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS);
    });
    expect(invalidatedKeys().sort()).toEqual(
      [
        JSON.stringify(["risk-history", "f1"]),
        JSON.stringify(["risk-scores", "f1"]),
        JSON.stringify(["telemetry-freshness", "f1"]),
      ].sort()
    );
  });

  it("a burst of 50 frames for one facility causes one refresh per query, not 50", () => {
    renderHook(() => useLive(), { wrapper });
    act(() => {
      latest().open();
      for (let i = 0; i < 50; i++) {
        latest().emit(updated("f1"));
        vi.advanceTimersByTime(2); // 100 ms in total, inside the debounce window
      }
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS);
    });
    expect(invalidate).toHaveBeenCalledTimes(3);
  });

  it("frames for two facilities refresh each once; a later frame starts a new window", () => {
    renderHook(() => useLive(), { wrapper });
    act(() => {
      latest().open();
      latest().emit(updated("f1"));
      latest().emit(updated("f2"));
      latest().emit(updated("f1"));
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS);
    });
    expect(invalidate).toHaveBeenCalledTimes(6);

    invalidate.mockClear();
    act(() => {
      vi.advanceTimersByTime(5000);
      latest().emit(updated("f1"));
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS);
    });
    expect(invalidate).toHaveBeenCalledTimes(3);
  });

  it("drops malformed, hostile and unknown frames and keeps working", () => {
    const { result } = renderHook(() => useLive(), { wrapper });
    act(() => {
      latest().open();
      latest().emit("{nope");
      latest().emit(null);
      latest().emit("a string");
      latest().emit({ type: "something_new", facility_id: "f1", asset_id: "a" });
      latest().emit({ type: "scores_updated", facility_id: 7, scored: 1, computed_at: "x" });
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS * 2);
    });
    expect(invalidate).not.toHaveBeenCalled();
    expect(result.current.alerts).toHaveLength(0);

    act(() => {
      latest().emit(anomaly);
    });
    expect(result.current.alerts).toHaveLength(1);
  });

  it("reconnects after a drop, reports not-connected meanwhile, and catches up once on reconnect", () => {
    const { result } = renderHook(() => useLive(), { wrapper });
    act(() => latest().open());
    expect(invalidate).not.toHaveBeenCalled(); // no catch-up on the very first connection

    act(() => latest().drop());
    expect(result.current.connected).toBe(false);
    expect(sockets()).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS);
    });
    expect(sockets()).toHaveLength(2);
    act(() => latest().open());
    expect(result.current.connected).toBe(true);

    act(() => {
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS);
    });
    // pushes sent while the socket was down were lost: every facility's queries refresh, once
    expect(invalidatedKeys().sort()).toEqual(
      [JSON.stringify(["risk-history"]), JSON.stringify(["risk-scores"]), JSON.stringify(["telemetry-freshness"])].sort()
    );
  });

  it("a connection that reports 'closed' twice, or flaps, never opens two sockets at once", () => {
    renderHook(() => useLive(), { wrapper });
    const first = latest();
    act(() => {
      first.drop();
      first.drop(); // a duplicate close event from the same socket
    });
    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS * 3);
    });
    expect(sockets()).toHaveLength(2); // one reconnect, not two

    for (let i = 0; i < 4; i++) {
      act(() => latest().drop());
      act(() => {
        vi.advanceTimersByTime(RECONNECT_DELAY_MS);
      });
    }
    expect(sockets()).toHaveLength(6); // exactly one new socket per drop
  });

  it("late events from a socket that was already replaced are ignored", () => {
    const { result } = renderHook(() => useLive(), { wrapper });
    const old = latest();
    act(() => old.open());
    act(() => old.drop());
    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS);
    });
    act(() => {
      old.emit(anomaly); // arrives after the new socket took over
      old.open();
    });
    expect(result.current.alerts).toHaveLength(0);
    expect(result.current.connected).toBe(false);
  });

  it("unmounting closes the socket and cancels a refresh that was waiting", () => {
    const { unmount } = renderHook(() => useLive(), { wrapper });
    act(() => {
      latest().open();
      latest().emit(updated("f1"));
    });
    const socket = latest();
    unmount();
    expect(socket.closed).toBe(true);

    act(() => {
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS * 4);
    });
    expect(invalidate).not.toHaveBeenCalled();
    expect(sockets()).toHaveLength(1); // and no reconnect after unmount
  });
});

describe("useFallbackInterval", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    setAccessToken("t");
    setAuthenticated(true);
    queryClient = new QueryClient();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("polls every minute when there is no live connection, and not at all while there is one", () => {
    const { result } = renderHook(() => useFallbackInterval(), { wrapper });
    expect(result.current).toBe(FALLBACK_POLL_MS); // connecting: not live yet
    expect(FALLBACK_POLL_MS).toBe(60_000);

    act(() => latest().open());
    expect(result.current).toBe(false);

    act(() => latest().drop());
    expect(result.current).toBe(FALLBACK_POLL_MS); // dropped: polling is back
  });

  it("with no provider at all (a page rendered alone) it polls", () => {
    const { result } = renderHook(() => useFallbackInterval());
    expect(result.current).toBe(FALLBACK_POLL_MS);
  });
});

describe("alerts reach any screen", () => {
  it("an alert from the shared connection renders as a toast wherever the shell is", async () => {
    const { AlertToast } = await import("../components/AlertToast");
    function Probe() {
      const { alerts, dismiss } = useLive();
      return <AlertToast alerts={alerts} onDismiss={dismiss} />;
    }
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    setAccessToken("t");
    setAuthenticated(true);
    queryClient = new QueryClient();
    try {
      render(<Probe />, { wrapper });
      act(() => {
        latest().open();
        latest().emit(anomaly);
      });
      expect(screen.getByRole("alert")).toHaveTextContent("vibration");
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
      setAccessToken(null);
    }
  });
});
