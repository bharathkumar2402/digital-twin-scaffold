import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "../lib/apiClient";
import type { AgentRunDetail, AgentRunRequest, AgentRunSummary } from "../types/api";

function runsQueryKey(facilityId: string) {
  return ["agent-runs", facilityId] as const;
}

// Newest first (the backend orders by created_at desc).
export function useAgentRuns(facilityId: string) {
  return useQuery({
    queryKey: runsQueryKey(facilityId),
    queryFn: () => apiFetch<AgentRunSummary[]>(`/facilities/${facilityId}/agent-runs`),
    enabled: Boolean(facilityId),
  });
}

// The full state snapshot (every agent's output) for one run.
export function useAgentRun(runId: string | null) {
  return useQuery({
    queryKey: ["agent-run", runId] as const,
    queryFn: () => apiFetch<AgentRunDetail>(`/agent-runs/${runId}`),
    enabled: Boolean(runId),
  });
}

// Triggers the 5-agent pipeline on the facility's real assets (takes several seconds) and
// returns the finished run, so the page can show it without a second round trip.
export function useRunAgentAnalysis(facilityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentRunRequest) =>
      apiFetch<AgentRunDetail>(`/facilities/${facilityId}/agent-runs`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (run) => {
      queryClient.setQueryData(["agent-run", run.id], run);
      void queryClient.invalidateQueries({ queryKey: runsQueryKey(facilityId) });
    },
  });
}
