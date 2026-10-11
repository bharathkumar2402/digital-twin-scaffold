import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GLOSSARY } from "../lib/glossary";
import { StatusBadge } from "./StatusBadge";
import { Term } from "./Term";

describe("StatusBadge", () => {
  it("shows the label and carries the reason and action in its tooltip", () => {
    render(
      <StatusBadge
        status={{ key: "critical", label: "Failing soon", action: "Schedule maintenance now", reasons: ["Risk score 95.8"], rank: 0 }}
      />
    );
    const badge = screen.getByText("Failing soon");
    expect(badge).toHaveClass("pill-high");
    expect(badge).toHaveAttribute("title", "Risk score 95.8. Schedule maintenance now.");
  });
});

describe("Term", () => {
  it("explains itself to keyboard and screen-reader users, and is not a button", () => {
    render(<Term term="rul">Remaining life</Term>);
    const term = screen.getByRole("note");
    expect(term).toHaveAttribute("tabindex", "0");
    expect(term).toHaveAttribute("data-tip", GLOSSARY.rul);
    expect(term).toHaveAccessibleName(`Remaining life: ${GLOSSARY.rul}`);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
