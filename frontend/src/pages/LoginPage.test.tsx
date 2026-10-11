import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../hooks/useAuth";
import { ApiError } from "../lib/apiClient";
import * as demoAccount from "../lib/demoAccount";
import { LoginPage } from "./LoginPage";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

const login = vi.fn();

function fillAndSubmit(tenant: string, email: string, password: string) {
  fireEvent.change(screen.getByLabelText(/tenant id/i), { target: { value: tenant } });
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("LoginPage", () => {
  beforeEach(() => {
    login.mockReset();
    vi.mocked(useAuth).mockReturnValue({ login } as unknown as ReturnType<typeof useAuth>);
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
  });

  it("trims whitespace pasted around the tenant id and email", async () => {
    login.mockResolvedValue(undefined);
    fillAndSubmit("  6763efaf-307e-40c2-8afa-05876770aef5\n", " demo@twinlabs.com ", "pw");
    await waitFor(() => expect(login).toHaveBeenCalled());
    expect(login).toHaveBeenCalledWith({
      tenantId: "6763efaf-307e-40c2-8afa-05876770aef5",
      email: "demo@twinlabs.com",
      password: "pw",
    });
  });

  it("calls a 401 a credentials problem", async () => {
    login.mockRejectedValue(new ApiError(401, "no"));
    fillAndSubmit("t", "a@b.com", "x");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid tenant ID, email or password");
  });

  it("does NOT call a server error a credentials problem", async () => {
    login.mockRejectedValue(new ApiError(500, "Internal Server Error"));
    fillAndSubmit("t", "a@b.com", "x");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("HTTP 500");
    expect(alert).not.toHaveTextContent("Invalid tenant ID");
  });

  it("says so when the API cannot be reached", async () => {
    login.mockRejectedValue(new TypeError("Failed to fetch"));
    fillAndSubmit("t", "a@b.com", "x");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });

  it("offers no demo button when there is no demo account", () => {
    expect(screen.queryByRole("button", { name: /use demo account/i })).not.toBeInTheDocument();
  });
});

describe("LoginPage demo account", () => {
  it("signs in with the demo account in one click", async () => {
    vi.spyOn(demoAccount, "getDemoAccount").mockReturnValue({
      tenantId: "d3500000-0000-4000-8000-000000000001",
      email: "demo@twinlabs.com",
      password: "demo-password-123",
    });
    const demoLogin = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAuth).mockReturnValue({ login: demoLogin } as unknown as ReturnType<typeof useAuth>);
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole("button", { name: /use demo account/i }));
    await waitFor(() => expect(demoLogin).toHaveBeenCalled());
    expect(demoLogin).toHaveBeenCalledWith({
      tenantId: "d3500000-0000-4000-8000-000000000001",
      email: "demo@twinlabs.com",
      password: "demo-password-123",
    });
    vi.restoreAllMocks();
  });
});
