import type { ReactElement } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";

export function RequireAuth({ children }: { children: ReactElement }): ReactElement {
  const { isAuthenticated, isInitializing } = useAuth();
  const location = useLocation();

  // Wait for the bootstrap refresh attempt (AuthProvider's mount effect) before
  // deciding to redirect - otherwise every page reload would bounce to /login for a
  // moment even when the HttpOnly refresh cookie is about to restore the session.
  if (isInitializing) {
    return (
      <div
        style={{
          width: "100vw",
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--color-bg)",
        }}
      >
        <p style={{ margin: 0, fontSize: 14, color: "var(--color-text-muted)" }}>Loading...</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  return children;
}
