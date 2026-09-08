import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { apiFetch, setAccessToken } from "../lib/apiClient";
import type { AccessTokenResponse, Role } from "../types/api";

interface LoginParams {
  tenantId: string;
  email: string;
  password: string;
}

interface AuthState {
  role: Role | null;
  isAuthenticated: boolean;
  login: (params: LoginParams) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

function decodeRole(accessToken: string): Role | null {
  // The role claim rides in the JWT payload (see backend/app/core/security.py's
  // create_access_token) - decoding it client-side is fine, it's not secret, only
  // the signature (which the backend verifies on every request) is.
  try {
    const payload = JSON.parse(atob(accessToken.split(".")[1])) as { role?: Role };
    return payload.role ?? null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const [role, setRole] = useState<Role | null>(null);

  const login = useCallback(async ({ tenantId, email, password }: LoginParams) => {
    const response = await apiFetch<AccessTokenResponse>("/login", {
      method: "POST",
      body: JSON.stringify({ tenant_id: tenantId, email, password }),
    });
    setAccessToken(response.access_token);
    setRole(decodeRole(response.access_token));
  }, []);

  const logout = useCallback(() => {
    setAccessToken(null);
    setRole(null);
  }, []);

  const value = useMemo(
    () => ({ role, isAuthenticated: role !== null, login, logout }),
    [role, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// Colocated with AuthProvider deliberately; they always change together.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
