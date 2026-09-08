import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import { nextPollInterval, useFacilityMapUpload } from "./useFacilityMapUpload";

vi.mock("../lib/apiClient", () => ({
  apiFetch: vi.fn(),
}));

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useFacilityMapUpload", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("fetches the upload status for the given facility/upload id", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      id: "u1",
      facility_id: "f1",
      original_filename: "plan.svg",
      format: "svg",
      status: "tiled",
      tile_prefix: "f1/u1",
      tile_url_template: "http://localhost:9000/facility-map-tiles/f1/u1/{z}/{x}/{y}.png",
    });

    const { result } = renderHook(() => useFacilityMapUpload("f1", "u1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/map/u1");
    expect(result.current.data?.status).toBe("tiled");
  });

  it("does not fetch when facilityId or uploadId is empty", () => {
    renderHook(() => useFacilityMapUpload("", "u1"), { wrapper });
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it.each(["tiled", "failed", "conversion_failed"] as const)(
    "stops polling once status is terminal (%s)",
    (status) => {
      expect(nextPollInterval(status)).toBe(false);
    }
  );

  it.each(["pending", "processing", "sanitized"] as const)(
    "keeps polling while status is non-terminal (%s)",
    (status) => {
      expect(nextPollInterval(status)).toBe(2000);
    }
  );

  it("keeps polling before the first response arrives", () => {
    expect(nextPollInterval(undefined)).toBe(2000);
  });
});
