import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, ApiError, getAccessToken, setAccessToken } from "./apiClient";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  beforeEach(() => {
    setAccessToken(null);
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches the in-memory access token as a Bearer header", async () => {
    setAccessToken("token-123");
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ok: true }));

    await apiFetch("/facilities/f1/map/u1");

    const [, init] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer token-123");
  });

  it("sends no Authorization header when there is no token", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ok: true }));

    await apiFetch("/facilities/f1/map/u1");

    const [, init] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.has("Authorization")).toBe(false);
  });

  it("on a 401, refreshes once via POST /refresh and retries the original request", async () => {
    setAccessToken("stale-token");
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh-token", token_type: "bearer" }))
      .mockResolvedValueOnce(jsonResponse({ status: "tiled" }));

    const result = await apiFetch<{ status: string }>("/facilities/f1/map/u1");

    expect(result).toEqual({ status: "tiled" });
    expect(getAccessToken()).toBe("fresh-token");
    expect(fetch).toHaveBeenCalledTimes(3);
    const refreshCall = vi.mocked(fetch).mock.calls[1];
    expect(refreshCall[0]).toContain("/refresh");
    const retryCall = vi.mocked(fetch).mock.calls[2];
    const retryHeaders = new Headers(retryCall[1]?.headers);
    expect(retryHeaders.get("Authorization")).toBe("Bearer fresh-token");
  });

  it("does not retry a second time if refresh itself fails", async () => {
    setAccessToken("stale-token");
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));

    await expect(apiFetch("/facilities/f1/map/u1")).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(getAccessToken()).toBeNull();
  });

  it("never tries to refresh a failed /login or /refresh call itself", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("bad credentials", { status: 401 }));

    await expect(apiFetch("/login", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
