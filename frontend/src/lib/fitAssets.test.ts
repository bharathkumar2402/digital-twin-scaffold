import { describe, expect, it } from "vitest";

import { assetBounds } from "./fitAssets";
import { pixelToLngLat } from "./mapCoords";

describe("assetBounds", () => {
  it("is null when there is nothing to frame", () => {
    expect(assetBounds([])).toBeNull();
  });

  it("contains every asset (south-west corner first)", () => {
    const assets = [
      { x: 520, y: 2700 },
      { x: 3200, y: 1500 },
      { x: 1900, y: 2650 },
    ];
    const [[west, south], [east, north]] = assetBounds(assets)!;
    for (const a of assets) {
      const [lng, lat] = pixelToLngLat(a.x, a.y);
      expect(lng).toBeGreaterThanOrEqual(west);
      expect(lng).toBeLessThanOrEqual(east);
      expect(lat).toBeGreaterThanOrEqual(south);
      expect(lat).toBeLessThanOrEqual(north);
    }
    expect(west).toBeLessThan(east);
    expect(south).toBeLessThan(north);
  });

  it("gives a zero-size box for a single asset instead of failing", () => {
    const [[w, s], [e, n]] = assetBounds([{ x: 100, y: 100 }])!;
    expect(w).toBe(e);
    expect(s).toBe(n);
  });
});
