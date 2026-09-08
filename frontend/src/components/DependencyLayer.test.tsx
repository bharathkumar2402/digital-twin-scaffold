import { render } from "@testing-library/react";
import type maplibregl from "maplibre-gl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Asset, AssetDependency } from "../types/api";
import { DependencyLayer } from "./DependencyLayer";

const setData = vi.fn();
let sourceExists = false;
let circleLayerExists = false;
const addSource = vi.fn(() => {
  sourceExists = true;
});
const addLayer = vi.fn();
const getSource = vi.fn(() => (sourceExists ? { setData } : undefined));
const getLayer = vi.fn(() => (circleLayerExists ? {} : undefined));

const fakeMap = {
  getSource,
  addSource,
  addLayer,
  getLayer,
} as unknown as maplibregl.Map;

const pump: Asset = {
  id: "pump-1",
  facility_id: "f1",
  name: "Pump 7",
  type: "pump",
  x: 100,
  y: 200,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
};

const tank: Asset = {
  id: "tank-1",
  facility_id: "f1",
  name: "Tank 1",
  type: "tank",
  x: 300,
  y: 400,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
};

const dependency: AssetDependency = {
  id: "d1",
  facility_id: "f1",
  parent_asset_id: "pump-1",
  child_asset_id: "tank-1",
};

describe("DependencyLayer", () => {
  beforeEach(() => {
    addSource.mockClear();
    addLayer.mockClear();
    setData.mockClear();
    getSource.mockClear();
    getLayer.mockClear();
    sourceExists = false;
    circleLayerExists = false;
  });

  it("adds a geojson source and line layer on mount", () => {
    render(<DependencyLayer map={fakeMap} assets={[pump, tank]} dependencies={[dependency]} />);

    expect(addSource).toHaveBeenCalledWith(
      "facility-asset-dependencies",
      expect.objectContaining({ type: "geojson" })
    );
    expect(addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ type: "line" }),
      undefined
    );
  });

  it("inserts the line layer below the asset circle layer when it already exists", () => {
    circleLayerExists = true;
    render(<DependencyLayer map={fakeMap} assets={[pump, tank]} dependencies={[dependency]} />);

    expect(addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ type: "line" }),
      "facility-assets-circle"
    );
  });

  it("renders a line feature connecting the parent and child asset positions", () => {
    render(<DependencyLayer map={fakeMap} assets={[pump, tank]} dependencies={[dependency]} />);

    expect(setData).toHaveBeenCalled();
    const data = setData.mock.calls.at(-1)?.[0] as GeoJSON.FeatureCollection;
    expect(data.features).toHaveLength(1);
    expect(data.features[0].geometry.type).toBe("LineString");
    expect(data.features[0].properties).toEqual({ id: "d1" });
  });

  it("drops an edge referencing an asset that no longer exists", () => {
    render(<DependencyLayer map={fakeMap} assets={[pump]} dependencies={[dependency]} />);

    const data = setData.mock.calls.at(-1)?.[0] as GeoJSON.FeatureCollection;
    expect(data.features).toHaveLength(0);
  });

  it("does not add the source/layer twice if they already exist", () => {
    const { rerender } = render(
      <DependencyLayer map={fakeMap} assets={[pump, tank]} dependencies={[dependency]} />
    );
    addSource.mockClear();
    addLayer.mockClear();

    rerender(<DependencyLayer map={fakeMap} assets={[pump, tank]} dependencies={[dependency]} />);

    expect(addSource).not.toHaveBeenCalled();
    expect(addLayer).not.toHaveBeenCalled();
  });
});
