import { describe, expect, it } from "vitest";

import { tabsForRole } from "./navigation";
import { canRunAnalyses } from "./roles";

describe("tabsForRole", () => {
  it.each(["superadmin", "tenant_admin", "facility_manager"] as const)(
    "%s sees every tab, with Analysis last",
    (role) => {
      expect(tabsForRole(role)).toEqual(["overview", "map", "assets", "analysis"]);
    }
  );

  it.each(["technician", "viewer"] as const)("%s does not see Analysis", (role) => {
    expect(tabsForRole(role)).toEqual(["overview", "map", "assets"]);
  });

  it("gives an unknown role the least-privileged set", () => {
    expect(tabsForRole(null)).toEqual(["overview", "map", "assets"]);
  });
});

describe("canRunAnalyses", () => {
  it.each([
    ["superadmin", true],
    ["tenant_admin", true],
    ["facility_manager", true],
    ["technician", false],
    ["viewer", false],
    [null, false],
  ] as const)("%s -> %s", (role, allowed) => {
    expect(canRunAnalyses(role)).toBe(allowed);
  });
});
