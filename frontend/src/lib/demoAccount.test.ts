import { describe, expect, it } from "vitest";

import { getDemoAccount } from "./demoAccount";

const vars = { VITE_DEMO_TENANT_ID: "t", VITE_DEMO_EMAIL: "e@x.com", VITE_DEMO_PASSWORD: "pw" };

describe("getDemoAccount", () => {
  it("is offered in the dev server when all three variables are set", () => {
    expect(getDemoAccount({ DEV: true, ...vars })).toEqual({
      tenantId: "t",
      email: "e@x.com",
      password: "pw",
    });
  });

  it("is never offered outside the dev server, even if the variables leak into a build", () => {
    expect(getDemoAccount({ DEV: false, ...vars })).toBeNull();
  });

  it.each(["VITE_DEMO_TENANT_ID", "VITE_DEMO_EMAIL", "VITE_DEMO_PASSWORD"] as const)(
    "is not offered when %s is missing",
    (missing) => {
      expect(getDemoAccount({ DEV: true, ...vars, [missing]: "" })).toBeNull();
    }
  );
});
