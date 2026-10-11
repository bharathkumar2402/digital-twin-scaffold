import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FacilityMap, RASTER_PROFILE_MAX_ZOOM } from "./FacilityMap";

const addSource = vi.fn();
const addLayer = vi.fn();
const addControl = vi.fn();
const remove = vi.fn();
const resize = vi.fn();
let loadCallback: (() => void) | undefined;

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor(_options: unknown) {}
    addControl = addControl;
    addSource = addSource;
    addLayer = addLayer;
    remove = remove;
    resize = resize;
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
    resize.mockClear();
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
        maxzoom: RASTER_PROFILE_MAX_ZOOM,
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


describe("FacilityMap container resizing", () => {
  const observe = vi.fn();
  const disconnect = vi.fn();
  let notify: (() => void) | undefined;

  beforeEach(() => {
    observe.mockClear();
    disconnect.mockClear();
    resize.mockClear();
    notify = undefined;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: () => void) {
          notify = callback;
        }
        observe = observe;
        disconnect = disconnect;
      }
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("re-measures the map whenever its container changes size, not just the window", () => {
    const { getByTestId } = render(<FacilityMap tileUrlTemplate="http://x/{z}/{x}/{y}.png" />);
    expect(observe).toHaveBeenCalledWith(getByTestId("maplibre-container"));
    expect(resize).not.toHaveBeenCalled();
    notify?.();
    expect(resize).toHaveBeenCalledTimes(1);
    notify?.();
    expect(resize).toHaveBeenCalledTimes(2);
  });

  it("stops observing when the map is removed", () => {
    const { unmount } = render(<FacilityMap tileUrlTemplate="http://x/{z}/{x}/{y}.png" />);
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
  });
});

describe("FacilityMap without ResizeObserver (older browsers, jsdom)", () => {
  it("still renders and unmounts cleanly", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    const { unmount } = render(<FacilityMap tileUrlTemplate="http://x/{z}/{x}/{y}.png" />);
    expect(() => unmount()).not.toThrow();
    vi.unstubAllGlobals();
  });
});
