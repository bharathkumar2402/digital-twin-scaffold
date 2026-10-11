import { latestCompletedRun } from "../lib/work";
import { useAgentRun, useAgentRuns } from "./useAgentRuns";

// The facility's most recent completed analysis, with its full output. `none` means no run has
// completed yet (a different state from still loading, and from failing to load).
export function useLatestPlan(facilityId: string) {
  const runs = useAgentRuns(facilityId);
  const latest = runs.data ? latestCompletedRun(runs.data) : null;
  const detail = useAgentRun(latest?.id ?? null);

  const isError = runs.isError || (latest !== null && detail.isError);
  const isPending = !isError && (runs.isPending || (latest !== null && detail.isPending));
  return {
    isPending,
    isError,
    none: runs.data !== undefined && latest === null,
    run: detail.data ?? null,
    retry: () => {
      void runs.refetch();
      if (latest) void detail.refetch();
    },
  };
}
