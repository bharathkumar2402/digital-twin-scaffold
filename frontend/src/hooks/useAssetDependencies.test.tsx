import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { AssetDependency } from "../types/api";
import {
  useAssetDependencies,
  useCreateAssetDependency,
  useDeleteAssetDependency,
} from "./useAssetDependencies";

vi.mock("../lib/apiClient", () => ({
  apiFetch: vi.fn(),
}));

const dependency: AssetDependency = {
  id: "d1",
  facility_id: "f1",
  parent_asset_id: "pump-1",
  child_asset_id: "tank-1",
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useAssetDependencies", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("fetches the dependency list for the given facility", async () => {
    vi.mocked(apiFetch).mockResolvedValue([dependency]);

    const { result } = renderHook(() => useAssetDependencies("f1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/asset-dependencies");
    expect(result.current.data).toEqual([dependency]);
  });

  it("does not fetch when facilityId is empty", () => {
    renderHook(() => useAssetDependencies(""), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
  });
});

describe("useCreateAssetDependency", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("POSTs the parent/child pair to the facility's asset-dependencies endpoint", async () => {
    vi.mocked(apiFetch).mockResolvedValue(dependency);

    const { result } = renderHook(() => useCreateAssetDependency("f1"), { wrapper });
    result.current.mutate({ parent_asset_id: "pump-1", child_asset_id: "tank-1" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/asset-dependencies", {
      method: "POST",
      body: JSON.stringify({ parent_asset_id: "pump-1", child_asset_id: "tank-1" }),
    });
  });
});

describe("useDeleteAssetDependency", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("DELETEs the given dependency id", async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeleteAssetDependency("f1"), { wrapper });
    result.current.mutate("d1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/asset-dependencies/d1", {
      method: "DELETE",
    });
  });
});
