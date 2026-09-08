import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { TelemetryReading } from "../types/api";
import { useAssetTelemetry } from "./useAssetTelemetry";

vi.mock("../lib/apiClient", () => ({
  apiFetch: vi.fn(),
}));

const reading: TelemetryReading = {
  sensor_type: "temperature",
  value: 42.5,
  unit: "celsius",
  timestamp: "2026-01-01T00:00:00Z",
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useAssetTelemetry", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("fetches telemetry for the given facility and asset", async () => {
    vi.mocked(apiFetch).mockResolvedValue([reading]);

    const { result } = renderHook(() => useAssetTelemetry("f1", "a1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/assets/a1/telemetry");
    expect(result.current.data).toEqual([reading]);
  });

  it("does not fetch when facilityId or assetId is empty", () => {
    renderHook(() => useAssetTelemetry("", "a1"), { wrapper });
    renderHook(() => useAssetTelemetry("f1", ""), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
