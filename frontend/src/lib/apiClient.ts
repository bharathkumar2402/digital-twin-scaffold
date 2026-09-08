// Never persists the access token to localStorage/sessionStorage (frontend/CLAUDE.md) -
// it lives only in this module's memory and is lost on page reload, same as the
// refresh cookie's HttpOnly-ness keeps it out of JS reach in the other direction.
import type { AccessTokenResponse } from "../types/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function refreshAccessToken(): Promise<boolean> {
  const response = await fetch(`${API_BASE_URL}/refresh`, {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    setAccessToken(null);
    return false;
  }
  const body = (await response.json()) as AccessTokenResponse;
  setAccessToken(body.access_token);
  return true;
}

// Every request rides `credentials: "include"` so the HttpOnly refresh cookie (set on
// login by the backend, scoped to /refresh) rides along if a 401 forces a refresh -
// harmless on requests that don't need it, since the cookie's own path scoping means
// the browser only actually attaches it to /refresh itself.
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  _isRetry = false
): Promise<T> {
  const headers = new Headers(options.headers);
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

  if (response.status === 401 && !_isRetry && path !== "/login" && path !== "/refresh") {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return apiFetch<T>(path, options, true);
    }
  }

  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(response.status, detail || response.statusText);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
