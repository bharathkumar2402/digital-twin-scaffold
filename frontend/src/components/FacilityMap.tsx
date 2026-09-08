import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

// gdal2tiles.py's "raster" profile (backend/app/sandbox/convert.py) has no real-world
// CRS - it tiles the floor-plan image as a plain quadtree over a synthetic extent
// centered at [0, 0], the same shape a normal Web Mercator pyramid uses, so a standard
// MapLibre raster source addresses it correctly as long as the scheme/zoom range match
// what gdal2tiles actually produced:
//   - scheme "tms": gdal2tiles' default y-axis convention (y=0 at the bottom), as
//     opposed to MapLibre's default "xyz" (y=0 at the top).
//   - maxzoom 4: matches DEFAULT_MAX_ZOOM in app/sandbox/convert.py. Not exposed via
//     the FacilityMapUploadStatusResponse schema, so this is a hand-kept constant
//     rather than a value read off the API - reopening that merged 2.4 contract for a
//     zoom-level int didn't seem worth it for this task, but if DEFAULT_MAX_ZOOM ever
//     changes on the backend, update this too.
// Exported for src/lib/mapCoords.ts (issue 2.6's asset pixel<->lnglat conversion) so
// that math stays pinned to the exact same zoom level this layer renders at, instead
// of a second hand-copied "4".
export const RASTER_PROFILE_MAX_ZOOM = 4;
const RASTER_SOURCE_ID = "facility-floor-plan";

interface FacilityMapProps {
  tileUrlTemplate: string;
  // Fired once the map + floor-plan raster layer are ready, handing back the raw
  // maplibregl.Map instance so a caller (AssetLayer, task 2.6) can attach its own
  // GeoJSON source/layers on top without this component needing to know about
  // assets at all.
  onMapLoad?: (map: maplibregl.Map) => void;
}

export function FacilityMap({ tileUrlTemplate, onMapLoad }: FacilityMapProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {},
        layers: [],
      },
      center: [0, 0],
      zoom: 0,
      minZoom: 0,
      maxZoom: RASTER_PROFILE_MAX_ZOOM,
      renderWorldCopies: false,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl());

    map.on("load", () => {
      map.addSource(RASTER_SOURCE_ID, {
        type: "raster",
        tiles: [tileUrlTemplate],
        tileSize: 256,
        scheme: "tms",
        minzoom: 0,
        maxzoom: RASTER_PROFILE_MAX_ZOOM,
      });
      map.addLayer({
        id: RASTER_SOURCE_ID,
        type: "raster",
        source: RASTER_SOURCE_ID,
      });
      onMapLoad?.(map);
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [tileUrlTemplate, onMapLoad]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} data-testid="maplibre-container" />;
}
