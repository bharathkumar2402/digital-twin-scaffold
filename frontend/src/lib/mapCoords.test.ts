import { describe, expect, it } from "vitest";

import { lngLatToPixel, pixelToLngLat } from "./mapCoords";

describe("pixelToLngLat / lngLatToPixel", () => {
  it("round-trips a pixel coordinate through lng/lat and back", () => {
    const [lng, lat] = pixelToLngLat(500, 300);
    const [x, y] = lngLatToPixel({ lng, lat });

    expect(x).toBeCloseTo(500, 6);
    expect(y).toBeCloseTo(300, 6);
  });

  it("maps the pixel origin (0, 0) to the top-left of the mercator grid", () => {
    const [lng, lat] = pixelToLngLat(0, 0);
    expect(lng).toBeCloseTo(-180, 6);
    // MapLibre's mercator y=0 corresponds to the north pole limit (~85.0511 deg).
    expect(lat).toBeCloseTo(85.05112878, 4);
  });

  it("increasing x moves lng eastward and increasing y moves lat southward", () => {
    const [lng0] = pixelToLngLat(0, 0);
    const [lng1] = pixelToLngLat(100, 0);
    expect(lng1).toBeGreaterThan(lng0);

    const [, lat0] = pixelToLngLat(0, 0);
    const [, lat1] = pixelToLngLat(0, 100);
    expect(lat1).toBeLessThan(lat0);
  });
});
