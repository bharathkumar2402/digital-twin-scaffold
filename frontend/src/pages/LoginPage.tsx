import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";

// Deliberately bare - this task's job is proving the map-rendering pipeline works
// end-to-end, not building the real login UX. Just enough to obtain an access token
// against the existing POST /login endpoint (backend/app/api/auth.py).
export function LoginPage(): React.JSX.Element {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [tenantId, setTenantId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login({ tenantId, email, password });
      navigate(searchParams.get("next") ?? "/");
    } catch {
      setError("Invalid email or password");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background:
          "radial-gradient(circle at top, #eef2ff 0%, var(--color-bg) 55%)",
      }}
    >
      <div style={{ width: "100%", maxWidth: 360 }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div
            style={{
              width: 44,
              height: 44,
              margin: "0 auto 16px",
              borderRadius: 12,
              background: "var(--color-primary)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
              fontSize: 18,
              boxShadow: "var(--shadow-md)",
            }}
          >
            DT
          </div>
          <h1 style={{ fontSize: 22 }}>Digital Twin</h1>
          <p style={{ fontSize: 13.5 }}>Sign in to view your facility's live map</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="card"
          style={{ padding: 28 }}
        >
          <div className="field">
            <label className="field-label" htmlFor="tenantId">
              Tenant ID
            </label>
            <input
              id="tenantId"
              className="input"
              value={tenantId}
              onChange={(event) => setTenantId(event.target.value)}
              autoComplete="organization"
              required
            />
          </div>

          <div className="field">
            <label className="field-label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              className="input"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
            />
          </div>

          <div className="field" style={{ marginBottom: error ? 12 : 20 }}>
            <label className="field-label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className="input"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </div>

          {error && (
            <p
              role="alert"
              style={{
                background: "var(--color-danger-soft)",
                border: "1px solid var(--color-danger-border)",
                color: "var(--color-danger)",
                borderRadius: "var(--radius-sm)",
                padding: "8px 10px",
                fontSize: 13,
                marginBottom: 16,
              }}
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={isSubmitting}
            style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
          >
            {isSubmitting ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
