import { describe, expect, it } from "vitest";

import { replaceAssetIds } from "./assetNames";

const PUMP = "96019989-b6d1-4fba-819a-541441f472da";
const names = new Map([[PUMP, "Primary Feed Pump 7"]]);

describe("replaceAssetIds", () => {
  it("replaces a known asset id with its name", () => {
    expect(replaceAssetIds(`Failure at root asset ${PUMP} threatens 4 assets`, names)).toBe(
      "Failure at root asset Primary Feed Pump 7 threatens 4 assets"
    );
  });

  it("replaces every occurrence, regardless of letter case", () => {
    expect(replaceAssetIds(`${PUMP} then ${PUMP.toUpperCase()}`, names)).toBe(
      "Primary Feed Pump 7 then Primary Feed Pump 7"
    );
  });

  it("leaves an id that is not in the facility visible instead of hiding it", () => {
    const stranger = "11111111-2222-3333-4444-555555555555";
    expect(replaceAssetIds(`asset ${stranger}`, names)).toBe(`asset ${stranger}`);
  });

  it("leaves text without ids, and things that only look like ids, untouched", () => {
    expect(replaceAssetIds("No assets here: 1234-5678", names)).toBe("No assets here: 1234-5678");
    expect(replaceAssetIds("", names)).toBe("");
  });
});
