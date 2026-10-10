import { ApiError } from "./apiClient";

// Say what actually went wrong. The page used to show "Invalid email or password" for every
// failure - including a server error, a server that was down, or a malformed tenant id - which
// sent people debugging credentials that were fine.
export function describeLoginError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Invalid tenant ID, email or password.";
    }
    if (error.status === 422) {
      return "Check the format: the tenant ID must be a UUID and the email a valid address.";
    }
    if (error.status >= 500) {
      return `The server had a problem (HTTP ${error.status}). This is not a credentials problem - check that the API is running and connected to its database.`;
    }
    return `Sign-in failed (HTTP ${error.status}).`;
  }
  if (error instanceof TypeError) {
    return "Could not reach the server. Check that the API is running.";
  }
  return "Sign-in failed.";
}
