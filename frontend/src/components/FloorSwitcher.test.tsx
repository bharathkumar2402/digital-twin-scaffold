import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { FloorBadge } from "../lib/floors";
import { FloorSwitcher } from "./FloorSwitcher";

const badges: FloorBadge[] = [
  { level: 0, name: "Ground floor", mapUploadId: "u0", assetCount: 17, highRisk: 2, alarms: 1 },
  { level: 1, name: "First floor", mapUploadId: "u1", assetCount: 16, highRisk: 0, alarms: 0 },
  { level: 2, name: "Roof", mapUploadId: "u2", assetCount: 11, highRisk: 0, alarms: 0 },
];

function renderSwitcher(list = badges, level: number | undefined = 1) {
  render(
    <MemoryRouter>
      <FloorSwitcher facilityId="fac" badges={list} currentLevel={level} />
    </MemoryRouter>
  );
}

describe("FloorSwitcher", () => {
  it("links each floor to its own map, top floor first", () => {
    renderSwitcher();
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/facilities/fac/map/u2",
      "/facilities/fac/map/u1",
      "/facilities/fac/map/u0",
    ]);
  });

  it("marks the floor being viewed as the current page", () => {
    renderSwitcher();
    expect(screen.getByRole("link", { name: /First floor/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Roof/ })).not.toHaveAttribute("aria-current");
  });

  it("says in words which floor has high-risk assets and alarms", () => {
    renderSwitcher();
    expect(screen.getByRole("link", { name: /Ground floor 2 high risk, 17 assets, 1 vibration alarms/ })).toBeInTheDocument();
  });

  it("renders nothing for a single-floor facility", () => {
    renderSwitcher([badges[0]], 0);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
  it("names each link with exactly its visible text (level, floor name, red count), then the details", () => {
    render(
      <MemoryRouter>
        <FloorSwitcher facilityId="f1" badges={badges} currentLevel={1} />
      </MemoryRouter>
    );
    expect(screen.getByRole("link", { name: "L0 Ground floor 2 high risk, 17 assets, 1 vibration alarms" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "L2 Roof 11 assets" })).toBeInTheDocument();
  });
});
