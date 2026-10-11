import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentRun, useAgentRuns, useRunAgentAnalysis } from "../hooks/useAgentRuns";
import type { AgentRunDetail, AgentRunSummary } from "../types/api";
import { AgentAnalysisPanel } from "./AgentAnalysisPanel";

vi.mock("../hooks/useAgentRuns", () => ({
  useAgentRuns: vi.fn(),
  useAgentRun: vi.fn(),
  useRunAgentAnalysis: vi.fn(),
}));

const summary = (id: string, status = "completed"): AgentRunSummary => ({
  id,
  tenant_id: "t",
  facility_id: "f",
  trigger: "user_query",
  status,
  duration_ms: 1000,
  validation_errors_json: [],
  decision_report: "report",
  confidence: 0.9,
  created_at: "2026-10-10T10:00:00Z",
});

const detail = (id: string): AgentRunDetail => ({
  ...summary(id),
  decision_report: `Report for ${id}`,
  state_snapshot_json: {
    risk_scores: null,
    maintenance_schedule: null,
    inventory_gaps: null,
    dispatch_routes: null,
    simulation_result: null,
  },
});

const mutate = vi.fn();

function setup(opts: {
  runs?: AgentRunSummary[] | undefined;
  runsError?: boolean;
  detailData?: AgentRunDetail | undefined;
  pending?: boolean;
  error?: Error | null;
}) {
  vi.mocked(useAgentRuns).mockReturnValue({
    data: opts.runs,
    isError: opts.runsError ?? false,
  } as unknown as ReturnType<typeof useAgentRuns>);
  vi.mocked(useAgentRun).mockReturnValue({
    data: opts.detailData,
    isPending: false,
    isError: false,
  } as unknown as ReturnType<typeof useAgentRun>);
  vi.mocked(useRunAgentAnalysis).mockReturnValue({
    mutate,
    isPending: opts.pending ?? false,
    isError: Boolean(opts.error),
    error: opts.error ?? null,
  } as unknown as ReturnType<typeof useRunAgentAnalysis>);
}

describe("AgentAnalysisPanel", () => {
  beforeEach(() => {
    mutate.mockReset();
    vi.mocked(useAgentRun).mockReset();
  });

  it("offers the Run button only to roles that may run analyses", () => {
    setup({ runs: [] });
    const { rerender } = render(
      <AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} />
    );
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeInTheDocument();

    rerender(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={false} />);
    expect(screen.queryByRole("button", { name: "Run analysis" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("What-if question")).not.toBeInTheDocument();
    expect(screen.getByText(/can view past analyses but not start one/)).toBeInTheDocument();
  });

  it("starts a what-if run with the trimmed question", () => {
    setup({ runs: [] });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} />);
    fireEvent.change(screen.getByLabelText("What-if question"), {
      target: { value: "  What if Pump 7 fails?  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(mutate).toHaveBeenCalledWith(
      { trigger: "user_query", user_query: "What if Pump 7 fails?" },
      expect.any(Object)
    );
  });

  it("starts a scheduled run when the question is blank", () => {
    setup({ runs: [] });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} />);
    fireEvent.change(screen.getByLabelText("What-if question"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(mutate).toHaveBeenCalledWith(
      { trigger: "scheduled", user_query: null },
      expect.any(Object)
    );
  });

  it("shows a busy state and blocks a second click while the run is in flight", () => {
    setup({ runs: [], pending: true });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} />);
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });

  it("shows why a run could not be started", () => {
    setup({ runs: [], error: new Error("403 Forbidden") });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not run the analysis: 403 Forbidden");
  });

  it("shows the newest past run by default and lets the user switch", () => {
    setup({ runs: [summary("new"), summary("old")], detailData: detail("new") });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={false} />);
    expect(vi.mocked(useAgentRun)).toHaveBeenLastCalledWith("new");
    expect(screen.getByText("Report for new")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Earlier analyses"), { target: { value: "old" } });
    expect(vi.mocked(useAgentRun)).toHaveBeenLastCalledWith("old");
  });

  describe("suggested questions", () => {
    const chips = ["What if Air Compressor AC-1 fails?", "What if CNC Machine CNC-2 fails?"];

    it("fills the question box when clicked, without running anything", () => {
      setup({ runs: [] });
      render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} suggestions={chips} />);
      fireEvent.click(screen.getByRole("button", { name: chips[1] }));
      expect(screen.getByLabelText("What-if question")).toHaveValue(chips[1]);
      expect(mutate).not.toHaveBeenCalled();
      // the user can then run it as usual
      fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
      expect(mutate).toHaveBeenCalledWith(
        { trigger: "user_query", user_query: chips[1] },
        expect.any(Object)
      );
    });

    it("is not offered to roles that cannot run analyses", () => {
      setup({ runs: [] });
      render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={false} suggestions={chips} />);
      expect(screen.queryByRole("group", { name: "Suggested questions" })).not.toBeInTheDocument();
    });

    it("is hidden when there are no suggestions, and disabled while a run is in flight", () => {
      setup({ runs: [] });
      const { rerender } = render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} />);
      expect(screen.queryByRole("group", { name: "Suggested questions" })).not.toBeInTheDocument();
      setup({ runs: [], pending: true });
      rerender(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={true} suggestions={chips} />);
      expect(screen.getByRole("button", { name: chips[0] })).toBeDisabled();
    });
  });

  it("says so when no analysis has ever been run", () => {
    setup({ runs: [] });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={false} />);
    expect(screen.getByText(/No analysis has been run for this facility yet/)).toBeInTheDocument();
  });

  it("surfaces a failure to load the history", () => {
    setup({ runs: undefined, runsError: true });
    render(<AgentAnalysisPanel facilityId="f" assetNames={new Map()} canRun={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load past analyses");
  });
});
