import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { AboutPage } from "./AboutPage";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../hooks/useFacilities", () => ({ useFacilities: vi.fn() }));

describe("AboutPage", () => {
  it("lists the models and their datasets", () => {
    vi.mocked(useAuth).mockReturnValue({ role: "viewer", logout: vi.fn() } as unknown as ReturnType<typeof useAuth>);
    vi.mocked(useFacilities).mockReturnValue({ data: [] } as unknown as ReturnType<typeof useFacilities>);
    render(
      <MemoryRouter>
        <AboutPage />
      </MemoryRouter>
    );
    expect(screen.getByRole("heading", { name: "About the models" })).toBeInTheDocument();
    expect(screen.getAllByRole("article").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/not how it will do on this facility/)).toBeInTheDocument();
  });
});
