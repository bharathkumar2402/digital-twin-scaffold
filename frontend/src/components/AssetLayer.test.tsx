import { render } from "@testing-library/react";
import type maplibregl from "maplibre-gl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Asset } from "../types/api";
import { AssetLayer } from "./AssetLayer";

const setData = vi.fn();
let sourceExists = false;
const addSource = vi.fn(() => {
  sourceExists = true;
});
const addLayer = vi.fn();
const getSource = vi.fn(() => (sourceExists ? { setData } : undefined));
const dragPanDisable = vi.fn();
const dragPanEnable = vi.fn();
const getCanvas = vi.fn(() => ({ style: {} }));

type Handler = (event: unknown) => void;
const handlers: Record<string, Handler[]> = {};
const layerHandlers: Record<string, Record<string, Handler[]>> = {};

function on(event: string, layerIdOrHandler: string | Handler, maybeHandler?: Handler) {
  if (typeof layerIdOrHandler === "string") {
    layerHandlers[event] ??= {};
    layerHandlers[event][layerIdOrHandler] ??= [];
    layerHandlers[event][layerIdOrHandler].push(maybeHandler as Handler);
  } else {
    handlers[event] ??= [];
    handlers[event].push(layerIdOrHandler);
  }
}

function off() {
  // no-op for these tests - cleanup correctness isn't under test here
}

function fireLayerEvent(event: string, layerId: string, payload: unknown) {
  layerHandlers[event]?.[layerId]?.forEach((handler) => handler(payload));
}

function fireMapEvent(event: string, payload: unknown) {
  handlers[event]?.forEach((handler) => handler(payload));
}

const fakeMap = {
  getSource,
  addSource,
  addLayer,
  on,
  off,
  dragPan: { disable: dragPanDisable, enable: dragPanEnable },
  getCanvas,
} as unknown as maplibregl.Map;

const asset: Asset = {
  id: "a1",
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

describe("AssetLayer", () => {
  beforeEach(() => {
    addSource.mockClear();
    addLayer.mockClear();
    setData.mockClear();
    getSource.mockClear();
    dragPanDisable.mockClear();
    dragPanEnable.mockClear();
    sourceExists = false;
    Object.keys(handlers).forEach((key) => delete handlers[key]);
    Object.keys(layerHandlers).forEach((key) => delete layerHandlers[key]);
  });

  it("adds a geojson source and circle layer on mount", () => {
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={vi.fn()}
      />
    );

    expect(addSource).toHaveBeenCalledWith(
      "facility-assets",
      expect.objectContaining({ type: "geojson" })
    );
    expect(addLayer).toHaveBeenCalledWith(expect.objectContaining({ type: "circle" }));
  });

  it("colors an unscored asset with the neutral 'no data' color, not a risk band", () => {
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={vi.fn()}
      />
    );

    const layerCall = addLayer.mock.calls[0][0] as { paint: { "circle-color": unknown[] } };
    const [, , unscoredColor] = layerCall.paint["circle-color"];
    expect(unscoredColor).toBe("#9e9e9e");

    const sourceCall = addSource.mock.calls[0][1] as {
      data: { features: { properties: { risk_score: number } }[] };
    };
    // No initial features are passed to addSource (empty facility state) - the
    // per-asset risk_score property is exercised via setData below instead.
    expect(sourceCall.data.features).toEqual([]);
  });

  it("writes each asset's risk score onto its feature so the step-color expression can band it", () => {
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map([["a1", 80]])}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={vi.fn()}
      />
    );

    expect(setData).toHaveBeenCalledWith(
      expect.objectContaining({
        features: [expect.objectContaining({ properties: expect.objectContaining({ risk_score: 80 }) })],
      })
    );
  });

  it("selects an asset when its feature is clicked, without also placing a new asset", () => {
    const onSelectAsset = vi.fn();
    const onPlaceNewAsset = vi.fn();
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={true}
        onSelectAsset={onSelectAsset}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={onPlaceNewAsset}
      />
    );

    fireLayerEvent("click", "facility-assets-circle", {
      features: [{ properties: { id: "a1" } }],
    });
    fireMapEvent("click", { lngLat: { lng: 0, lat: 0 } });

    expect(onSelectAsset).toHaveBeenCalledWith("a1");
    expect(onPlaceNewAsset).not.toHaveBeenCalled();
  });

  it("does not commit a move for a plain click-and-release with no drag movement", () => {
    // Regression test: found by manually exercising this in a real browser - a plain
    // click on an asset (mousedown then mouseup at the same spot, no mousemove
    // between them) was firing a no-op onMoveAsset to the same coordinates.
    const onMoveAsset = vi.fn();
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={onMoveAsset}
        onPlaceNewAsset={vi.fn()}
      />
    );

    fireLayerEvent("mousedown", "facility-assets-circle", {
      features: [{ properties: { id: "a1" } }],
      preventDefault: vi.fn(),
      point: { x: 100, y: 100 },
    });
    fireMapEvent("mouseup", { lngLat: { lng: 0, lat: 0 }, point: { x: 100, y: 100 } });

    expect(onMoveAsset).not.toHaveBeenCalled();
  });

  it("does not commit a move for a mousedown/mouseup pair with only sub-threshold jitter", () => {
    // A real click's mousedown and mouseup can still sandwich a mousemove of a pixel
    // or so with zero intent to drag - this must stay a no-op below DRAG_THRESHOLD_PX.
    const onMoveAsset = vi.fn();
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={onMoveAsset}
        onPlaceNewAsset={vi.fn()}
      />
    );

    fireLayerEvent("mousedown", "facility-assets-circle", {
      features: [{ properties: { id: "a1" } }],
      preventDefault: vi.fn(),
      point: { x: 100, y: 100 },
    });
    fireMapEvent("mousemove", { lngLat: { lng: 0, lat: 0 }, point: { x: 101, y: 100 } });
    fireMapEvent("mouseup", { lngLat: { lng: 0, lat: 0 }, point: { x: 101, y: 100 } });

    expect(onMoveAsset).not.toHaveBeenCalled();
  });

  it("places a new asset on a plain map click while in add mode", () => {
    const onPlaceNewAsset = vi.fn();
    render(
      <AssetLayer
        map={fakeMap}
        assets={[]}
        riskScores={new Map()}
        addMode={true}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={onPlaceNewAsset}
      />
    );

    fireMapEvent("click", { lngLat: { lng: 0, lat: 0 } });

    expect(onPlaceNewAsset).toHaveBeenCalledTimes(1);
    const [x, y] = onPlaceNewAsset.mock.calls[0] as [number, number];
    expect(Number.isFinite(x)).toBe(true);
    expect(Number.isFinite(y)).toBe(true);
  });

  it("ignores a plain map click when not in add mode", () => {
    const onPlaceNewAsset = vi.fn();
    render(
      <AssetLayer
        map={fakeMap}
        assets={[]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={onPlaceNewAsset}
      />
    );

    fireMapEvent("click", { lngLat: { lng: 0, lat: 0 } });

    expect(onPlaceNewAsset).not.toHaveBeenCalled();
  });

  it("drags an asset: mousedown disables panning, mouseup commits the new position and does not also place a new asset", () => {
    const onMoveAsset = vi.fn();
    const onPlaceNewAsset = vi.fn();
    render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={true}
        onSelectAsset={vi.fn()}
        onMoveAsset={onMoveAsset}
        onPlaceNewAsset={onPlaceNewAsset}
      />
    );

    const preventDefault = vi.fn();
    fireLayerEvent("mousedown", "facility-assets-circle", {
      features: [{ properties: { id: "a1" } }],
      preventDefault,
      point: { x: 100, y: 100 },
    });
    expect(preventDefault).toHaveBeenCalled();
    expect(dragPanDisable).toHaveBeenCalled();

    fireMapEvent("mousemove", { lngLat: { lng: 1, lat: 1 }, point: { x: 140, y: 100 } });
    expect(setData).toHaveBeenCalled();

    fireMapEvent("mouseup", { lngLat: { lng: 1, lat: 1 }, point: { x: 140, y: 100 } });
    expect(dragPanEnable).toHaveBeenCalled();
    expect(onMoveAsset).toHaveBeenCalledTimes(1);
    expect(onMoveAsset.mock.calls[0][0]).toBe("a1");

    fireMapEvent("click", { lngLat: { lng: 1, lat: 1 } });
    expect(onPlaceNewAsset).not.toHaveBeenCalled();
  });

  it("does not add the source/layers twice if they already exist", () => {
    const { rerender } = render(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={vi.fn()}
      />
    );
    addSource.mockClear();
    addLayer.mockClear();

    rerender(
      <AssetLayer
        map={fakeMap}
        assets={[asset]}
        riskScores={new Map()}
        addMode={false}
        onSelectAsset={vi.fn()}
        onMoveAsset={vi.fn()}
        onPlaceNewAsset={vi.fn()}
      />
    );

    expect(addSource).not.toHaveBeenCalled();
    expect(addLayer).not.toHaveBeenCalled();
  });
});
