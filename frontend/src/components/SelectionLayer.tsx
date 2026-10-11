import type { GeoJSONSource } from "maplibre-gl";
import { useEffect } from "react";

import { pixelToLngLat } from "../lib/mapCoords";
import type { Asset } from "../types/api";

const SOURCE_ID = "facility-selection";
const LAYER_ID = "facility-selection-ring";

function toData(asset: Asset | null) {
  return {
    type: "FeatureCollection" as const,
    features: asset
      ? [
          {
            type: "Feature" as const,
            geometry: { type: "Point" as const, coordinates: pixelToLngLat(asset.x, asset.y) },
            properties: {},
          },
        ]
      : [],
  };
}

// A ring around the selected asset so it can be found again on a busy floor.
export function SelectionLayer({ map, asset }: { map: maplibregl.Map | null; asset: Asset | null }): null {
  useEffect(() => {
    if (!map || map.getSource(SOURCE_ID)) return;
    map.addSource(SOURCE_ID, { type: "geojson", data: toData(null) });
    map.addLayer({
      id: LAYER_ID,
      type: "circle",
      source: SOURCE_ID,
      paint: {
        "circle-radius": 20,
        "circle-color": "#2563eb",
        "circle-opacity": 0,
        "circle-stroke-width": 3,
        "circle-stroke-color": "#2563eb",
      },
    });
  }, [map]);

  useEffect(() => {
    const source = map?.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(toData(asset));
  }, [map, asset]);

  return null;
}
