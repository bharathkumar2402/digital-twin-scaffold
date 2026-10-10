import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/apiClient";
import type { AgentRunDetail, AgentRunSummary } from "../types/api";
import { useAgentRun, useAgentRuns, useRunAgentAnalysis } from "./useAgentRuns";

vi.mock("../lib/apiClient", () => ({
  apiFetch: vi.fn(),
}));

const summary: AgentRunSummary = {
  id: "run-1",
  tenant_id: "t1",
  facility_id: "f1",
  trigger: "user_query",
  status: "completed",
  duration_ms: 1200,
  validation_errors_json: [],
  decision_report: "report",
  confidence: 0.9,
  created_at: "2026-10-10T10:00:00Z",
};

const detail: AgentRunDetail = {
  ...summary,
  state_snapshot_json: {
    risk_scores: [],
    maintenance_schedule: [],
    inventory_gaps: [],
    dispatch_routes: [],
    simulation_result: null,
  },
};

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("agent run hooks", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists the facility's runs", async () => {
    vi.mocked(apiFetch).mockResolvedValue([summary]);
    const { result } = renderHook(() => useAgentRuns("f1"), { wrapper: wrapperFor(makeClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/agent-runs");
    expect(result.current.data).toEqual([summary]);
  });

  it("does not fetch without a facility id", () => {
    renderHook(() => useAgentRuns(""), { wrapper: wrapperFor(makeClient()) });
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("fetches one run's full snapshot only once a run is selected", async () => {
    const { result, rerender } = renderHook(({ id }) => useAgentRun(id), {
      wrapper: wrapperFor(makeClient()),
      initialProps: { id: null as string | null },
    });
    expect(apiFetch).not.toHaveBeenCalled();

    vi.mocked(apiFetch).mockResolvedValue(detail);
    rerender({ id: "run-1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/agent-runs/run-1");
  });

  it("surfaces a failed fetch as an error instead of empty data", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAgentRuns("f1"), { wrapper: wrapperFor(makeClient()) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });

  it("POSTs the query, caches the finished run and refreshes the history", async () => {
    vi.mocked(apiFetch).mockResolvedValue(detail);
    const client = makeClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useRunAgentAnalysis("f1"), { wrapper: wrapperFor(client) });

    await act(async () => {
      await result.current.mutateAsync({ trigger: "user_query", user_query: "What if Pump 7 fails?" });
    });

    expect(apiFetch).toHaveBeenCalledWith("/facilities/f1/agent-runs", {
      method: "POST",
      body: JSON.stringify({ trigger: "user_query", user_query: "What if Pump 7 fails?" }),
    });
    expect(client.getQueryData(["agent-run", "run-1"])).toEqual(detail);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["agent-runs", "f1"] });
  });

  it("does not cache or refresh anything when the run request fails", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("403"));
    const client = makeClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useRunAgentAnalysis("f1"), { wrapper: wrapperFor(client) });

    await act(async () => {
      await result.current.mutateAsync({ trigger: "scheduled", user_query: null }).catch(() => undefined);
    });

    expect(invalidate).not.toHaveBeenCalled();
    expect(client.getQueryData(["agent-run", "run-1"])).toBeUndefined();
  });
});
