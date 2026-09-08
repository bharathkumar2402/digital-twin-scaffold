import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FacilityMap } from "./FacilityMap";

const addSource = vi.fn();
const addLayer = vi.fn();
const addControl = vi.fn();
const remove = vi.fn();
let loadCallback: (() => void) | undefined;

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor(_options: unknown) {}
    addControl = addControl;
    addSource = addSource;
    addLayer = addLayer;
    remove = remove;
    on(event: string, callback: () => void) {
      if (event === "load") {
        loadCallback = callback;
      }
    }
  }
  class FakeNavigationControl {}
  return { default: { Map: FakeMap, NavigationControl: FakeNavigationControl } };
});

vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));

describe("FacilityMap", () => {
  beforeEach(() => {
    addSource.mockClear();
    addLayer.mockClear();
    remove.mockClear();
    loadCallback = undefined;
  });

  it("adds a TMS raster source pointed at the given tile URL template once the map loads", () => {
    const tileUrlTemplate = "http://localhost:9000/facility-map-tiles/f1/u1/{z}/{x}/{y}.png";
    render(<FacilityMap tileUrlTemplate={tileUrlTemplate} />);

    loadCallback?.();

    expect(addSource).toHaveBeenCalledWith(
      "facility-floor-plan",
      expect.objectContaining({
        type: "raster",
        tiles: [tileUrlTemplate],
        scheme: "tms",
        maxzoom: 4,
      })
    );
    expect(addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "facility-floor-plan", type: "raster" })
    );
  });

  it("removes the map instance on unmount", () => {
    const { unmount } = render(
      <FacilityMap tileUrlTemplate="http://localhost:9000/facility-map-tiles/f1/u1/{z}/{x}/{y}.png" />
    );
    unmount();
    expect(remove).toHaveBeenCalledTimes(1);
  });
});
