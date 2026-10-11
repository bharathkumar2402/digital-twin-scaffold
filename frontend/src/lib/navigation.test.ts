import { describe, expect, it } from "vitest";

import { landingTab, tabsForRole } from "./navigation";
import { canRunAnalyses } from "./roles";

describe("tabsForRole", () => {
  it.each(["superadmin", "tenant_admin", "facility_manager"] as const)(
    "%s sees every tab, with Analysis last",
    (role) => {
      expect(tabsForRole(role)).toEqual(["overview", "map", "assets", "work", "analysis"]);
    }
  );

  it("starts a technician from Work and does not show them Analysis", () => {
    expect(tabsForRole("technician")).toEqual(["work", "map", "assets", "overview"]);
  });

  it("gives a viewer read-only tabs, without Analysis", () => {
    expect(tabsForRole("viewer")).toEqual(["overview", "map", "assets", "work"]);
  });

  it("gives an unknown role the least-privileged set (no Work, no Analysis)", () => {
    expect(tabsForRole(null)).toEqual(["overview", "map", "assets"]);
  });
});

describe("landingTab", () => {
  it.each([
    ["technician", "work"],
    ["facility_manager", "overview"],
    ["tenant_admin", "overview"],
    ["superadmin", "overview"],
    ["viewer", "overview"],
    [null, "overview"],
  ] as const)("%s lands on %s", (role, tab) => {
    expect(landingTab(role)).toBe(tab);
  });

  it("only ever lands on a tab that role can see", () => {
    for (const role of ["technician", "viewer", "facility_manager", "tenant_admin", "superadmin", null] as const) {
      expect(tabsForRole(role)).toContain(landingTab(role));
    }
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
