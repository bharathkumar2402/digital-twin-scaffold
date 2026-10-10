import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MODEL_CARDS } from "../lib/modelCards";
import { ModelCards } from "./ModelCards";

describe("ModelCards", () => {
  it("names each model's dataset, its result and its limits", () => {
    render(<ModelCards />);
    for (const title of ["Failure risk", "Remaining useful life", "Vibration anomaly"]) {
      const card = screen.getByRole("article", { name: title });
      expect(within(card).getByText(/Data:/)).toBeInTheDocument();
      expect(within(card).getByText(/Limits:/)).toBeInTheDocument();
    }
    expect(screen.getAllByText(/AI4I 2020/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/C-MAPSS/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/IMS/).length).toBeGreaterThan(0);
  });

  it("does not overclaim: states the weak spots and that the numbers are not live", () => {
    render(<ModelCards />);
    expect(screen.getByText(/Does not clearly beat a plain RMS threshold/)).toBeInTheDocument();
    expect(screen.getByText(/not data from this facility/)).toBeInTheDocument();
    expect(screen.getByText(/Metrics as of/)).toBeInTheDocument();
    expect(screen.getByText(/none is an LLM/)).toBeInTheDocument();
    expect(screen.getByText(/rule-of-thumb heuristic/)).toBeInTheDocument();
  });

  it("every card carries at least one limitation", () => {
    for (const card of MODEL_CARDS) expect(card.limitations.length).toBeGreaterThan(0);
  });
});
