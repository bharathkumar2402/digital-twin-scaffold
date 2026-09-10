import type { GeoJSONSource, MapLayerMouseEvent, MapMouseEvent } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { lngLatToPixel, pixelToLngLat } from "../lib/mapCoords";
import type { Asset } from "../types/api";

// Assets render as a plain GeoJSON circle layer, never a hand-positioned DOM element
// (frontend/CLAUDE.md: "assets are GeoJSON point layers, never hand-drawn SVG shapes
// positioned with CSS"). PROJECT_PLAN.md's eventual design calls for custom icons by
// asset type via a sprite sheet - out of scope here (no icon pipeline exists yet
// anywhere in this repo); a status-colored circle stands in for that until an icon
// set lands. A symbol layer with a text-field label was tried and dropped: MapLibre
// requires a style-level `glyphs` (font) URL for any text-field layer, and this
// project's map style (FacilityMap.tsx) deliberately has none - PROJECT_PLAN.md's
// design is a private, self-hosted map with no external services, and this repo has
// no self-hosted glyphs/font server to point at. Confirmed live in a real browser:
// without `glyphs`, MapLibre logs "text-field requires a style glyphs property" and
// silently drops the layer rather than throwing, so it would have quietly rendered
// nothing anyway.
const SOURCE_ID = "facility-assets";
const CIRCLE_LAYER_ID = "facility-assets-circle";
// A real click (mouse never really stationary) still fires a stray mousemove or two
// of a pixel or so between mousedown and mouseup - confirmed by exercising this in an
// actual browser, where an exact-mousemove-count check falsely treated a plain click
// as a drag. Only screen-pixel displacement past this threshold counts as an
// intentional drag.
const DRAG_THRESHOLD_PX = 3;

// Risk-score marker color (issue 3.7) - replaces the old status-based fill (status
// stays visible in the asset detail panel instead). Bands mirror
// backend/app/services/risk_inference_service.py's score_facility, which clamps
// scores into [0, 100]: 0-33 low, 34-66 medium, 67-100 high.
const RISK_COLOR_LOW = "#2e7d32";
const RISK_COLOR_MEDIUM = "#ed6c02";
const RISK_COLOR_HIGH = "#c62828";
// An asset with no risk-score row yet (never scored) gets a neutral grey rather than
// defaulting into one of the risk bands above, so "no data" can't be misread as "low
// risk".
const RISK_COLOR_UNSCORED = "#9e9e9e";
// Sentinel written into each feature's `risk_score` property when the asset has no
// score in `riskScores` - MapLibre expressions can't carry `null`/`undefined` through
// a `step` expression cleanly, so this stands in for "no data" and is matched first,
// below the real [0, 100] range.
const UNSCORED_SENTINEL = -1;

function toFeatureCollection(assets: Asset[], riskScores: Map<string, number>) {
  return {
    type: "FeatureCollection" as const,
    features: assets.map((asset) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: pixelToLngLat(asset.x, asset.y) },
      properties: {
        id: asset.id,
        name: asset.name,
        type: asset.type,
        status: asset.status,
        risk_score: riskScores.get(asset.id) ?? UNSCORED_SENTINEL,
      },
    })),
  };
}

interface AssetLayerProps {
  map: maplibregl.Map | null;
  assets: Asset[];
  // Latest risk score (0-100) per asset id, from useRiskScores - an asset absent from
  // this map has never been scored yet.
  riskScores: Map<string, number>;
  addMode: boolean;
  onSelectAsset: (assetId: string) => void;
  onMoveAsset: (assetId: string, x: number, y: number) => void;
  onPlaceNewAsset: (x: number, y: number) => void;
}

export function AssetLayer({
  map,
  assets,
  riskScores,
  addMode,
  onSelectAsset,
  onMoveAsset,
  onPlaceNewAsset,
}: AssetLayerProps): null {
  const draggingAssetIdRef = useRef<string | null>(null);
  // Screen-pixel position of the mousedown that started the current drag - compared
  // against each mousemove/mouseup to distinguish an actual drag from a plain
  // click-and-release (see DRAG_THRESHOLD_PX and handleMouseUp).
  const dragStartPointRef = useRef<{ x: number; y: number } | null>(null);
  const hasDraggedRef = useRef(false);
  // Guards the generic map "click" (add-new-asset) handler against also firing for a
  // click that a layer-specific handler (select, or the mouseup ending a drag) already
  // handled - MapLibre has no automatic stopPropagation between a layer click handler
  // and the map's own click handler, both fire for the same event.
  const suppressNextMapClickRef = useRef(false);

  // Latest callback props, read from inside long-lived map event listeners registered
  // once below - avoids re-registering (and re-adding the source/layers) on every
  // parent re-render just because a callback identity changed.
  const callbacksRef = useRef({ onSelectAsset, onMoveAsset, onPlaceNewAsset });
  callbacksRef.current = { onSelectAsset, onMoveAsset, onPlaceNewAsset };
  const addModeRef = useRef(addMode);
  addModeRef.current = addMode;
  // Read inside handleMouseMove's live-drag preview below - that handler is
  // registered once (effect depends only on `map`), so it must not close over
  // `assets` directly or it would redraw the drag preview against a stale list.
  const assetsRef = useRef(assets);
  assetsRef.current = assets;
  // Same rationale as assetsRef - read inside the long-lived drag-preview handler
  // below rather than closed over directly.
  const riskScoresRef = useRef(riskScores);
  riskScoresRef.current = riskScores;

  useEffect(() => {
    if (!map) {
      return;
    }

    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, { type: "geojson", data: toFeatureCollection([], new Map()) });
      map.addLayer({
        id: CIRCLE_LAYER_ID,
        type: "circle",
        source: SOURCE_ID,
        paint: {
          "circle-radius": 8,
          "circle-color": [
            "step",
            ["get", "risk_score"],
            RISK_COLOR_UNSCORED,
            0,
            RISK_COLOR_LOW,
            34,
            RISK_COLOR_MEDIUM,
            67,
            RISK_COLOR_HIGH,
          ],
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });
    }

    const getSource = () => map.getSource(SOURCE_ID) as GeoJSONSource | undefined;

    const handleMouseDown = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      const assetId = feature?.properties?.id as string | undefined;
      if (!assetId) {
        return;
      }
      event.preventDefault();
      draggingAssetIdRef.current = assetId;
      dragStartPointRef.current = { x: event.point.x, y: event.point.y };
      hasDraggedRef.current = false;
      map.dragPan.disable();
      map.getCanvas().style.cursor = "grabbing";
    };

    const pixelFromLngLat = (event: MapMouseEvent) => {
      const [x, y] = lngLatToPixel(event.lngLat);
      return { x, y };
    };

    const exceedsDragThreshold = (event: MapMouseEvent) => {
      const start = dragStartPointRef.current;
      if (!start) {
        return true;
      }
      const dx = event.point.x - start.x;
      const dy = event.point.y - start.y;
      return Math.hypot(dx, dy) > DRAG_THRESHOLD_PX;
    };

    const handleMouseMove = (event: MapMouseEvent) => {
      const draggingAssetId = draggingAssetIdRef.current;
      if (!draggingAssetId) {
        return;
      }
      const source = getSource();
      if (!source) {
        return;
      }
      if (exceedsDragThreshold(event)) {
        hasDraggedRef.current = true;
      }
      const updated = assetsRef.current.map((asset) =>
        asset.id === draggingAssetId ? { ...asset, ...pixelFromLngLat(event) } : asset
      );
      source.setData(toFeatureCollection(updated, riskScoresRef.current));
    };

    const handleMouseUp = (event: MapMouseEvent) => {
      const draggingAssetId = draggingAssetIdRef.current;
      if (!draggingAssetId) {
        return;
      }
      draggingAssetIdRef.current = null;
      dragStartPointRef.current = null;
      map.dragPan.enable();
      map.getCanvas().style.cursor = "";
      // A plain click (mousedown+mouseup with no meaningful pointer displacement)
      // must not commit a no-op "move" to the same coordinates - only an actual drag
      // does. A pixel-count check isn't reliable here: a real click's mousedown and
      // mouseup can still sandwich a mousemove of a pixel or so with zero intent to
      // drag (confirmed by exercising this in an actual browser), so this checks
      // displacement against DRAG_THRESHOLD_PX instead of "any movement at all".
      if (!hasDraggedRef.current) {
        return;
      }
      suppressNextMapClickRef.current = true;
      const { x, y } = pixelFromLngLat(event);
      callbacksRef.current.onMoveAsset(draggingAssetId, x, y);
    };

    const handleAssetClick = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      const assetId = feature?.properties?.id as string | undefined;
      if (!assetId) {
        return;
      }
      suppressNextMapClickRef.current = true;
      callbacksRef.current.onSelectAsset(assetId);
    };

    const handleMapClick = (event: MapMouseEvent) => {
      if (suppressNextMapClickRef.current) {
        suppressNextMapClickRef.current = false;
        return;
      }
      if (!addModeRef.current) {
        return;
      }
      const { x, y } = pixelFromLngLat(event);
      callbacksRef.current.onPlaceNewAsset(x, y);
    };

    map.on("mousedown", CIRCLE_LAYER_ID, handleMouseDown);
    map.on("mousemove", handleMouseMove);
    map.on("mouseup", handleMouseUp);
    map.on("click", CIRCLE_LAYER_ID, handleAssetClick);
    map.on("click", handleMapClick);

    return () => {
      map.off("mousedown", CIRCLE_LAYER_ID, handleMouseDown);
      map.off("mousemove", handleMouseMove);
      map.off("mouseup", handleMouseUp);
      map.off("click", CIRCLE_LAYER_ID, handleAssetClick);
      map.off("click", handleMapClick);
    };
    // Re-registers only when the map instance itself changes - `assets`, the
    // callbacks, and `addMode` are all read through refs above instead of being
    // effect dependencies, precisely so this doesn't tear down/rebuild the
    // source+layers+listeners on every render.
  }, [map]);

  useEffect(() => {
    if (!map) {
      return;
    }
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    if (source && !draggingAssetIdRef.current) {
      source.setData(toFeatureCollection(assets, riskScores));
    }
  }, [map, assets, riskScores]);

  return null;
}
