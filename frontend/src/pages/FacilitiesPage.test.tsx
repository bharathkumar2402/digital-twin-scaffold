import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { FacilitiesPage } from "./FacilitiesPage";

vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));
vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function setFacilities(value: Partial<ReturnType<typeof useFacilities>>) {
  vi.mocked(useFacilities).mockReturnValue({
    data: undefined,
    isPending: false,
    isError: false,
    ...value,
  } as ReturnType<typeof useFacilities>);
}

function renderPage() {
  render(
    <MemoryRouter>
      <FacilitiesPage />
    </MemoryRouter>
  );
}

describe("FacilitiesPage", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({ logout: vi.fn() } as unknown as ReturnType<typeof useAuth>);
  });

  it("lists each facility with links to its map and results", () => {
    setFacilities({
      data: [
        { id: "f1", name: "Demo Plant 1", latest_map_upload_id: "u1", asset_count: 6, floors: [] },
        { id: "f2", name: "Annex", latest_map_upload_id: null, asset_count: 1, floors: [] },
      ],
    });
    renderPage();

    const demo = screen.getByRole("article", { name: "Demo Plant 1" });
    expect(within(demo).getByText("6 assets")).toBeInTheDocument();
    expect(within(demo).getByRole("link", { name: "Open map" })).toHaveAttribute(
      "href",
      "/facilities/f1/map/u1"
    );
    expect(within(demo).getByRole("link", { name: "Results" })).toHaveAttribute(
      "href",
      "/facilities/f1/results"
    );
  });

  it("does not offer a map for a facility that has no floor plan, and says so", () => {
    setFacilities({ data: [{ id: "f2", name: "Annex", latest_map_upload_id: null, asset_count: 1, floors: [] }] });
    renderPage();
    const annex = screen.getByRole("article", { name: "Annex" });
    expect(within(annex).queryByRole("link", { name: "Open map" })).not.toBeInTheDocument();
    expect(within(annex).getByText(/1 asset \u00b7 no floor plan yet/)).toBeInTheDocument();
    expect(within(annex).getByRole("link", { name: "Results" })).toBeInTheDocument();
  });

  it("explains an empty account, a load failure and loading", () => {
    setFacilities({ data: [] });
    const { unmount } = render(
      <MemoryRouter>
        <FacilitiesPage />
      </MemoryRouter>
    );
    expect(screen.getByText(/no facilities on this account/)).toBeInTheDocument();
    unmount();

    setFacilities({ isError: true });
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load your facilities");
  });
});
