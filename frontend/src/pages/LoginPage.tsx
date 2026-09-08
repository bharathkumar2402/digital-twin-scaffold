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
    <form onSubmit={handleSubmit} style={{ maxWidth: 320, margin: "4rem auto" }}>
      <h1>Sign in</h1>
      <label htmlFor="tenantId">Tenant ID</label>
      <input
        id="tenantId"
        value={tenantId}
        onChange={(event) => setTenantId(event.target.value)}
        required
      />
      <label htmlFor="email">Email</label>
      <input
        id="email"
        type="email"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        required
      />
      <label htmlFor="password">Password</label>
      <input
        id="password"
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        required
      />
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Signing in..." : "Sign in"}
      </button>
    </form>
  );
}
