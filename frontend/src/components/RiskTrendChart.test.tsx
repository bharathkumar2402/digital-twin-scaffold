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
    expect(screen.getByRole("group", { name: /^Risk over the last 3 scores: from 10\.0 to 85\.0\./ })).toBeInTheDocument();
    const text = container.querySelector("svg")?.textContent ?? "";
    for (const label of ["10.0", "85.0", "34", "67"]) expect(text).toContain(label);
    expect(container.querySelectorAll("rect").length).toBe(3); // low / medium / high zones
    // the zones are explained in words under the chart, not crammed into it
    expect(screen.getByText(/Zones:/)).toHaveTextContent("low under 34, medium 34–66, high 67 and above.");
  });

  it("labels every dot with its own time and score", () => {
    render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85)]} />);
    const dots = screen.getAllByRole("img");
    expect(dots).toHaveLength(3);
    for (const dot of dots) expect(dot.getAttribute("aria-label")).toMatch(/risk \d+\.\d/);
    expect(dots[1].getAttribute("aria-label")).toContain("risk 40.0");
  });

  it("is a single tab stop, not one per dot", () => {
    const { container } = render(
      <RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85), day(3, 90)]} />
    );
    const tabbable = Array.from(container.querySelectorAll("[tabindex]")).filter(
      (el) => el.getAttribute("tabindex") === "0"
    );
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0]).toBe(screen.getByRole("group", { name: /Risk over the last 4 scores/ }));
  });

  it("explains the keyboard controls in its accessible name", () => {
    render(<RiskTrendChart points={[day(0, 10), day(1, 40)]} />);
    expect(screen.getByRole("group", { name: /arrow keys/ })).toBeInTheDocument();
  });

  describe("keyboard", () => {
    const points = [day(0, 10), day(1, 40), day(2, 85)];
    const chart = () => screen.getByRole("group", { name: /Risk over the last 3 scores/ });
    const live = () => screen.getByTestId("chart-live");

    it("starts on the latest score when focused, and announces it", () => {
      render(<RiskTrendChart points={points} />);
      fireEvent.focus(chart());
      expect(live()).toHaveTextContent("risk 85.0");
      expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("risk 85.0");
    });

    it("moves with the arrow keys, announces each point, and stops at the ends", () => {
      render(<RiskTrendChart points={points} />);
      fireEvent.focus(chart());
      fireEvent.keyDown(chart(), { key: "ArrowLeft" });
      expect(live()).toHaveTextContent("risk 40.0");
      fireEvent.keyDown(chart(), { key: "ArrowLeft" });
      expect(live()).toHaveTextContent("risk 10.0");
      fireEvent.keyDown(chart(), { key: "ArrowLeft" }); // already at the first point
      expect(live()).toHaveTextContent("risk 10.0");
      fireEvent.keyDown(chart(), { key: "ArrowRight" });
      expect(live()).toHaveTextContent("risk 40.0");
      fireEvent.keyDown(chart(), { key: "ArrowRight" });
      fireEvent.keyDown(chart(), { key: "ArrowRight" }); // already at the last point
      expect(live()).toHaveTextContent("risk 85.0");
    });

    it("jumps with Home and End and lets go with Escape", () => {
      render(<RiskTrendChart points={points} />);
      fireEvent.focus(chart());
      fireEvent.keyDown(chart(), { key: "Home" });
      expect(live()).toHaveTextContent("risk 10.0");
      fireEvent.keyDown(chart(), { key: "End" });
      expect(live()).toHaveTextContent("risk 85.0");
      fireEvent.keyDown(chart(), { key: "Escape" });
      expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();
      expect(live()).toBeEmptyDOMElement();
    });

    it("ignores other keys, so Tab and typing behave normally", () => {
      render(<RiskTrendChart points={points} />);
      fireEvent.focus(chart());
      expect(fireEvent.keyDown(chart(), { key: "Tab" })).toBe(true); // not prevented
      expect(live()).toHaveTextContent("risk 85.0");
    });

    it("clears the highlight when focus leaves the chart", () => {
      render(<RiskTrendChart points={points} />);
      fireEvent.focus(chart());
      fireEvent.blur(chart());
      expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();
    });
  });

  it("shows a tooltip on hover and hides it afterwards", () => {
    render(<RiskTrendChart points={[day(0, 10), day(1, 40), day(2, 85)]} />);
    const dots = screen.getAllByRole("img");
    expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();

    fireEvent.mouseEnter(dots[1]);
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("risk 40.0");
    fireEvent.mouseLeave(dots[1]);
    expect(screen.queryByTestId("chart-tooltip")).not.toBeInTheDocument();

    // (keyboard users get the same tooltip through arrow keys: see the "keyboard" tests)
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
