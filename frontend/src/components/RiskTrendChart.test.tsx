import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RiskHistoryPoint } from "../types/api";
import { RiskTrendChart } from "./RiskTrendChart";

const day = (d: number, score: number, rul: number | null = null): RiskHistoryPoint => ({
  computed_at: new Date(Date.UTC(2026, 9, 1 + d, 12)).toISOString(),
  score,
  rul_cycles: rul,
});

describe("RiskTrendChart", () => {
  it("describes the whole series for screen readers and shows labelled ends and the three zones", () => {
    const { container } = render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85)]} />);
    expect(screen.getByRole("group", { name: "Risk over the last 3 scores: from 10.0 to 85.0" })).toBeInTheDocument();
    const text = container.querySelector("svg")?.textContent ?? "";
    for (const label of ["10.0", "85.0", "34", "67"]) expect(text).toContain(label);
    expect(container.querySelectorAll("rect").length).toBe(3); // low / medium / high zones
    // the zones are explained in words under the chart, not crammed into it
    expect(screen.getByText(/Zones:/)).toHaveTextContent("low under 34, medium 34–66, high 67 and above.");
  });

  it("makes every dot focusable with its own time-and-score label", () => {
    render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85)]} />);
    const dots = screen.getAllByRole("img");
    expect(dots).toHaveLength(3);
    for (const dot of dots) {
      expect(dot).toHaveAttribute("tabindex", "0");
      expect(dot.getAttribute("aria-label")).toMatch(/risk \d+\.\d/);
    }
    expect(dots[1].getAttribute("aria-label")).toContain("risk 40.0");
  });

  it("shows a tooltip on hover and on keyboard focus, and hides it afterwards", () => {
    render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85)]} />);
    const dots = screen.getAllByRole("img");
    expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();

    fireEvent.mouseEnter(dots[1]);
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("risk 40.0");
    fireEvent.mouseLeave(dots[1]);
    expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();

    fireEvent.focus(dots[2]);
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("risk 85.0");
    fireEvent.blur(dots[2]);
    expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();
  });

  it("marks where the asset crossed into high risk and says when", () => {
    const { container } = render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 75), day(3, 95)]} />);
    expect(container.querySelector("svg")?.textContent).toContain("entered high risk");
    expect(screen.getByText(/Crossed into high risk \(67\+\) on/)).toBeInTheDocument();
  });

  it("has no crossing marker for an asset that was never high, or high the whole time", () => {
    const { container, rerender } = render(<RiskTrendChart points={[day(0, 10), day(1, 40)]} />);
    expect(container.querySelector("svg")?.textContent).not.toContain("entered high risk");
    rerender(<RiskTrendChart points={[day(0, 90), day(1, 95)]} />);
    expect(container.querySelector("svg")?.textContent).not.toContain("entered high risk");
    expect(screen.queryByText(/Crossed into high risk/)).not.toBeInTheDocument();
  });

  it("states the change in words, with direction, for a moving series", () => {
    render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85)]} />);
    expect(screen.getByText(/▲ up 45\.0 pts in 24 h/)).toBeInTheDocument();
  });

  it("says plainly that nothing changed, instead of drawing a story, for a flat series", () => {
    render(<RiskTrendChart points={[day(0, 95.8), day(1, 95.8), day(2, 95.8)]} />);
    expect(screen.getByText("No change across the last 3 scores (95.8).")).toBeInTheDocument();
    expect(screen.queryByText(/pts in/)).not.toBeInTheDocument();
  });

  it("shows how remaining life moved when scores carry it", () => {
    render(<RiskTrendChart points={[day(0, 10, 120), day(1, 50, 60), day(2, 90, 3.6)]} />);
    expect(screen.getByText(/Remaining life: 120\.0 → 3\.6 cycles\./)).toBeInTheDocument();
  });

  it("says nothing about remaining life when it did not really change", () => {
    render(<RiskTrendChart points={[day(0, 10, 3.1), day(1, 50, 3.1), day(2, 90, 3.3)]} />);
    expect(screen.queryByText(/Remaining life/)).not.toBeInTheDocument();
  });

  it("puts the high-risk marker's label on the inside, so it cannot run off the right edge", () => {
    const { container } = render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 75), day(3, 95)]} />);
    const label = Array.from(container.querySelectorAll("text")).find((t) => t.textContent === "entered high risk");
    expect(label?.getAttribute("text-anchor")).toBe("end"); // marker is in the right half
    const early = render(<RiskTrendChart points={[day(0, 90), day(1, 10), day(2, 80), day(3, 12), day(4, 11), day(5, 10)]} />);
    expect(early.container.querySelector("svg")?.textContent).not.toContain("entered high risk"); // not high now
  });

  it("says so for a single score rather than drawing a meaningless line", () => {
    const { container } = render(<RiskTrendChart points={[day(0, 42)]} />);
    expect(screen.getByText("Only one score so far (42.0).")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders nothing without scores", () => {
    const { container } = render(<RiskTrendChart points={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("keeps every drawn point inside the chart for out-of-range scores", () => {
    const { container } = render(<RiskTrendChart points={[day(0, -30), day(1, 250)]} />);
    for (const circle of Array.from(container.querySelectorAll("circle"))) {
      const cy = Number(circle.getAttribute("cy"));
      expect(cy).toBeGreaterThanOrEqual(0);
      expect(cy).toBeLessThanOrEqual(132);
    }
  });
});
