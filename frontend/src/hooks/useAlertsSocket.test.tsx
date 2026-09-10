import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../lib/apiClient";
import { useAlertsSocket } from "./useAlertsSocket";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
    this.onclose?.();
  }

  emitMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

describe("useAlertsSocket", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    setAccessToken("test-access-token");
  });

  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("connects with the access token as a query param", () => {
    renderHook(() => useAlertsSocket());

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0]?.url).toContain("token=test-access-token");
    expect(FakeWebSocket.instances[0]?.url).toContain("/ws/alerts");
  });

  it("appends an alert when a message arrives", async () => {
    const { result } = renderHook(() => useAlertsSocket());
    const socket = FakeWebSocket.instances[0]!;

    act(() => {
      socket.emitMessage({
        asset_id: "asset-1",
        sensor_type: "vibration",
        value: 999.0,
        z_score: 12.3,
        triggered_at: "2026-01-01T00:00:00Z",
      });
    });

    await waitFor(() => expect(result.current.alerts).toHaveLength(1));
    expect(result.current.alerts[0]?.asset_id).toBe("asset-1");
  });

  it("dismiss removes the alert by id", async () => {
    const { result } = renderHook(() => useAlertsSocket());
    const socket = FakeWebSocket.instances[0]!;

    act(() => {
      socket.emitMessage({
        asset_id: "asset-1",
        sensor_type: "vibration",
        value: 999.0,
        z_score: null,
        triggered_at: "2026-01-01T00:00:00Z",
      });
    });
    await waitFor(() => expect(result.current.alerts).toHaveLength(1));

    act(() => {
      result.current.dismiss(result.current.alerts[0]!.id);
    });
    await waitFor(() => expect(result.current.alerts).toHaveLength(0));
  });

  it("does not connect when there is no access token", () => {
    setAccessToken(null);
    renderHook(() => useAlertsSocket());
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});
