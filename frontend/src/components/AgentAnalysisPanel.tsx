import { useState } from "react";

import { useAgentRun, useAgentRuns, useRunAgentAnalysis } from "../hooks/useAgentRuns";
import { AgentRunResult } from "./AgentRunResult";

interface AgentAnalysisPanelProps {
  facilityId: string;
  // asset id -> name, from the facility's real asset list.
  assetNames: Map<string, string>;
  // Mirrors the backend's run roles (superadmin / tenant_admin / facility_manager); the
  // server still enforces it, this only avoids offering a button that would 403.
  canRun: boolean;
}

const EXAMPLE_QUERY = "What if Primary Feed Pump 7 fails?";

function runLabel(run: { created_at: string; trigger: string; status: string }): string {
  return `${new Date(run.created_at).toLocaleString()} · ${run.trigger} · ${run.status}`;
}

export function AgentAnalysisPanel({
  facilityId,
  assetNames,
  canRun,
}: AgentAnalysisPanelProps): React.JSX.Element {
  const [query, setQuery] = useState("");
  const [chosenRunId, setChosenRunId] = useState<string | null>(null);

  const runs = useAgentRuns(facilityId);
  const analyse = useRunAgentAnalysis(facilityId);

  // Until the user picks one, show the newest run; right after a run, show that one.
  const selectedRunId = chosenRunId ?? runs.data?.[0]?.id ?? null;
  const detail = useAgentRun(selectedRunId);

  function start(): void {
    const trimmed = query.trim();
    analyse.mutate(
      trimmed
        ? { trigger: "user_query", user_query: trimmed }
        : { trigger: "scheduled", user_query: null },
      { onSuccess: (run) => setChosenRunId(run.id) }
    );
  }

  return (
    <div>
      {canRun ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            start();
          }}
          style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}
        >
          <input
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Optional what-if, e.g. "${EXAMPLE_QUERY}"`}
            aria-label="What-if question"
            style={{ flex: 1, minWidth: 260 }}
            disabled={analyse.isPending}
          />
          <button type="submit" className="btn btn-primary" disabled={analyse.isPending}>
            {analyse.isPending ? "Running…" : "Run analysis"}
          </button>
        </form>
      ) : (
        <p style={{ fontSize: 13 }}>
          Your role can view past analyses but not start one.
        </p>
      )}

      {analyse.isPending && (
        <p role="status" aria-busy="true" style={{ fontSize: 13 }}>
          Running the five agents on this facility&rsquo;s assets &ndash; this takes several
          seconds.
        </p>
      )}
      {analyse.isError && (
        <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
          Could not run the analysis: {analyse.error.message}
        </p>
      )}

      {runs.isError && (
        <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
          Could not load past analyses.
        </p>
      )}
      {runs.data && runs.data.length > 0 && (
        <label style={{ display: "block", fontSize: 13, marginBottom: 12 }}>
          Past analyses{" "}
          <select
            value={selectedRunId ?? ""}
            onChange={(event) => setChosenRunId(event.target.value)}
            aria-label="Past analyses"
          >
            {runs.data.map((run) => (
              <option key={run.id} value={run.id}>
                {runLabel(run)}
              </option>
            ))}
          </select>
        </label>
      )}

      {runs.data && runs.data.length === 0 && !analyse.isPending && (
        <p style={{ fontSize: 13 }}>No analysis has been run for this facility yet.</p>
      )}

      {detail.isPending && selectedRunId && <p style={{ fontSize: 13 }}>Loading analysis...</p>}
      {detail.isError && (
        <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
          Could not load this analysis.
        </p>
      )}
      {detail.data && <AgentRunResult run={detail.data} assetNames={assetNames} />}
    </div>
  );
}
