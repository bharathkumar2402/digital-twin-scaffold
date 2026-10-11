import { describe, expect, it } from "vitest";

import { headingLevels, outlineProblems } from "./a11y";

// The outline checker is what guards every page, so it gets its own tests.
describe("outlineProblems", () => {
  it("accepts a normal outline", () => {
    expect(outlineProblems([1, 2, 3, 3, 2, 3, 4])).toEqual([]);
    expect(outlineProblems([1])).toEqual([]);
  });

  it("rejects no h1, two h1s, and an outline that does not start at h1", () => {
    expect(outlineProblems([2, 3])).toEqual([
      "expected exactly one h1, found 0",
      "first heading is h2, not h1",
    ]);
    expect(outlineProblems([1, 2, 1])).toEqual(["expected exactly one h1, found 2"]);
    expect(outlineProblems([])).toEqual(["expected exactly one h1, found 0"]);
  });

  it("rejects a skipped level on the way down but allows jumping back up", () => {
    expect(outlineProblems([1, 3])).toEqual(["h1 is followed by h3 (skips a level)"]);
    expect(outlineProblems([1, 2, 3, 4, 2])).toEqual([]);
  });
});

describe("headingLevels", () => {
  it("lists heading levels in document order", () => {
    const root = document.createElement("div");
    root.innerHTML = "<h1>a</h1><section><h2>b</h2><h3>c</h3></section><h2>d</h2>";
    expect(headingLevels(root)).toEqual([1, 2, 3, 2]);
  });
});
