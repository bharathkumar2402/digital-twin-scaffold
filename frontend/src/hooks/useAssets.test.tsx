import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { Asset } from "../types/api";
import { useAssets, useCreateAsset, useDeleteAsset, useUpdateAsset } from "./useAssets";

vi.mock("../lib/apiClient", () => ({
  apiFetch: vi.fn(),
}));

const asset: Asset = {
  id: "a1",
  facility_id: "f1",
  name: "Pump 7",
  type: "pump",
  x: 10,
  y: 20,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useAssets", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("fetches the asset list for the given facility", async () => {
    vi.mocked(apiFetch).mockResolvedValue([asset]);

    const { result } = renderHook(() => useAssets("f1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/assets");
    expect(result.current.data).toEqual([asset]);
  });

  it("does not fetch when facilityId is empty", () => {
    renderHook(() => useAssets(""), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
  });
});

describe("useCreateAsset", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("POSTs the new asset body to the facility's assets endpoint", async () => {
    vi.mocked(apiFetch).mockResolvedValue(asset);

    const { result } = renderHook(() => useCreateAsset("f1"), { wrapper });
    result.current.mutate({ name: "Pump 7", type: "pump", x: 10, y: 20 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/assets", {
      method: "POST",
      body: JSON.stringify({ name: "Pump 7", type: "pump", x: 10, y: 20 }),
    });
  });
});

describe("useUpdateAsset", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("PATCHes only the given fields to the asset's endpoint", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...asset, x: 99 });

    const { result } = renderHook(() => useUpdateAsset("f1"), { wrapper });
    result.current.mutate({ assetId: "a1", body: { x: 99, y: 5 } });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/assets/a1", {
      method: "PATCH",
      body: JSON.stringify({ x: 99, y: 5 }),
    });
  });
});

describe("useDeleteAsset", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("DELETEs the given asset id", async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeleteAsset("f1"), { wrapper });
    result.current.mutate("a1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/assets/a1", { method: "DELETE" });
  });
});
