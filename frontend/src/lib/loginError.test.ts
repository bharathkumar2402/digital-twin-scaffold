import { describe, expect, it } from "vitest";

import { ApiError } from "./apiClient";
import { describeLoginError } from "./loginError";

describe("describeLoginError", () => {
  it("blames credentials only for a 401", () => {
    expect(describeLoginError(new ApiError(401, "nope"))).toMatch(/Invalid tenant ID, email or password/);
  });

  it("explains a malformed tenant id or email (422) instead of calling it a bad password", () => {
    const message = describeLoginError(new ApiError(422, "unprocessable"));
    expect(message).toMatch(/UUID/);
    expect(message).not.toMatch(/Invalid tenant ID, email or password/);
  });

  it.each([500, 502, 503])("says a %s is a server problem, not a credentials problem", (status) => {
    const message = describeLoginError(new ApiError(status, "boom"));
    expect(message).toContain(`HTTP ${status}`);
    expect(message).toMatch(/not a credentials problem/);
  });

  it("reports an unreachable server (fetch rejects with a TypeError)", () => {
    expect(describeLoginError(new TypeError("Failed to fetch"))).toMatch(/Could not reach the server/);
  });

  it("falls back to a neutral message for anything else", () => {
    expect(describeLoginError(new ApiError(418, "teapot"))).toBe("Sign-in failed (HTTP 418).");
    expect(describeLoginError("weird")).toBe("Sign-in failed.");
  });
});
