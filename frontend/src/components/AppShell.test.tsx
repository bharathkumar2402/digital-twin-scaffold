import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { AppShell } from "./AppShell";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));

function setFacility(latestMap: string | null) {
  vi.mocked(useFacilities).mockReturnValue({
    data: [{ id: "f1", name: "Meridian", latest_map_upload_id: latestMap, asset_count: 1, floors: [] }],
  } as unknown as ReturnType<typeof useFacilities>);
}

function renderShell(role: string | null, path = "/facilities/f1/overview", inFacility = true) {
  vi.mocked(useAuth).mockReturnValue({ role, logout: vi.fn() } as unknown as ReturnType<typeof useAuth>);
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppShell facilityId={inFacility ? "f1" : undefined}>
        <p>content</p>
      </AppShell>
    </MemoryRouter>
  );
}

const tabNames = () =>
  within(screen.getByRole("navigation", { name: "Facility" }))
    .getAllByText(/^(Overview|Work|Map|Assets|Analysis)$/)
    .map((el) => el.textContent);

describe("AppShell tabs", () => {
  beforeEach(() => setFacility("u9"));

  it.each(["facility_manager", "tenant_admin", "superadmin"])("%s sees every tab", (role) => {
    renderShell(role);
    expect(tabNames()).toEqual(["Overview", "Map", "Assets", "Work", "Analysis"]);
  });

  it("puts a technician's Work first and hides Analysis", () => {
    renderShell("technician");
    expect(tabNames()).toEqual(["Work", "Map", "Assets", "Overview"]);
    expect(screen.queryByRole("link", { name: "Analysis" })).not.toBeInTheDocument();
  });

  it("gives a viewer the read-only tabs, without Analysis", () => {
    renderShell("viewer");
    expect(tabNames()).toEqual(["Overview", "Map", "Assets", "Work"]);
  });

  it("gives an unknown role the least-privileged tabs", () => {
    renderShell(null);
    expect(tabNames()).toEqual(["Overview", "Map", "Assets"]);
  });

  it("points each tab at its route, with the map's real upload id, and marks the current one", () => {
    renderShell("facility_manager", "/facilities/f1/assets");
    const nav = screen.getByRole("navigation", { name: "Facility" });
    expect(within(nav).getByText("Meridian")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/facilities/f1/overview");
    expect(within(nav).getByRole("link", { name: "Map" })).toHaveAttribute("href", "/facilities/f1/map/u9");
    expect(within(nav).getByRole("link", { name: "Assets" })).toHaveClass("nav-active");
    expect(within(nav).getByRole("link", { name: "Analysis" })).toHaveAttribute("href", "/facilities/f1/analysis");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveClass("nav-active");
  });

  it("disables the Map tab (not a link) when the facility has no floor plan yet", () => {
    setFacility(null);
    renderShell("facility_manager");
    const nav = screen.getByRole("navigation", { name: "Facility" });
    expect(within(nav).queryByRole("link", { name: "Map" })).not.toBeInTheDocument();
    expect(within(nav).getByText("Map")).toHaveClass("nav-disabled");
  });

  it("offers About the models from every page and shows no tabs outside a facility", () => {
    renderShell("viewer", "/about", false);
    expect(screen.getByRole("link", { name: "About the models" })).toHaveAttribute("href", "/about");
    expect(screen.queryByRole("navigation", { name: "Facility" })).not.toBeInTheDocument();
  });
});
