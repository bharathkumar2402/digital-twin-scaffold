import type { GeoJSONSource } from "maplibre-gl";
import { useEffect } from "react";

import { pixelToLngLat } from "../lib/mapCoords";
import type { Asset, AssetDependency } from "../types/api";

// Dependency edges render as a GeoJSON line layer between the two assets' positions,
// same reasoning as AssetLayer.tsx's circle layer (frontend/CLAUDE.md: GeoJSON layers,
// never hand-drawn SVG/CSS shapes) extended from points to edges.
const SOURCE_ID = "facility-asset-dependencies";
const LINE_LAYER_ID = "facility-asset-dependencies-line";

function toFeatureCollection(dependencies: AssetDependency[], assetsById: Map<string, Asset>) {
  const features = dependencies.flatMap((dependency) => {
    const parent = assetsById.get(dependency.parent_asset_id);
    const child = assetsById.get(dependency.child_asset_id);
    if (!parent || !child) {
      // An edge whose asset was deleted out from under it (the delete route doesn't
      // cascade into asset_dependencies) - drop it from the map rather than crash;
      // it still shows up as a dangling row wherever dependencies are listed.
      return [];
    }
    return [
      {
        type: "Feature" as const,
        geometry: {
          type: "LineString" as const,
          coordinates: [pixelToLngLat(parent.x, parent.y), pixelToLngLat(child.x, child.y)],
        },
        properties: { id: dependency.id },
      },
    ];
  });
  return { type: "FeatureCollection" as const, features };
}

interface DependencyLayerProps {
  map: maplibregl.Map | null;
  assets: Asset[];
  dependencies: AssetDependency[];
}

export function DependencyLayer({ map, assets, dependencies }: DependencyLayerProps): null {
  useEffect(() => {
    if (!map) {
      return;
    }
    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, { type: "geojson", data: toFeatureCollection([], new Map()) });
      // Inserted below the asset circle layer if it already exists, so edges render
      // under asset markers rather than on top of them.
      const beforeId = map.getLayer("facility-assets-circle") ? "facility-assets-circle" : undefined;
      map.addLayer(
        {
          id: LINE_LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          paint: {
            "line-color": "#607d8b",
            "line-width": 2,
            "line-dasharray": [2, 1],
          },
        },
        beforeId
      );
    }
  }, [map]);

  useEffect(() => {
    if (!map) {
      return;
    }
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    if (!source) {
      return;
    }
    const assetsById = new Map(assets.map((asset) => [asset.id, asset]));
    source.setData(toFeatureCollection(dependencies, assetsById));
  }, [map, assets, dependencies]);

  return null;
}
