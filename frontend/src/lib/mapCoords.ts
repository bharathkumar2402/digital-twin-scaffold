import maplibregl from "maplibre-gl";

import { RASTER_PROFILE_MAX_ZOOM } from "../components/FacilityMap";

const TILE_SIZE = 256;

/**
 * Converts an asset's local-pixel (x, y) - pixel offsets from the top-left of the
 * rasterized floor-plan image at its native/maxzoom resolution (see
 * backend/app/models/asset.py and backend/app/sandbox/convert.py) - into the
 * [lng, lat] MapLibre needs to place a GeoJSON point at the same visual spot as
 * that pixel in the floor-plan raster layer.
 *
 * gdal2tiles.py's "raster" profile (`-p raster`) addresses tiles using the same
 * standard XYZ/Web-Mercator tile grid as an ordinary slippy map - it just imprints
 * the floor plan's own pixels onto that grid instead of reprojecting real imagery -
 * so the pixel at (x, y) in the full maxzoom-resolution image sits at the same
 * normalized Mercator position MapLibre's raster layer renders that tile region at.
 * `maplibregl.MercatorCoordinate` is MapLibre's own definition of that normalized
 * [0, 1] space (y=0 at the north pole, same top-left-origin convention as image
 * pixels), so routing through it keeps this self-consistent with how the floor-plan
 * layer itself is drawn, rather than re-deriving the Mercator projection formula
 * by hand and risking a transcription bug that would silently misplace every asset.
 */
export function pixelToLngLat(x: number, y: number): [number, number] {
  const scale = 2 ** RASTER_PROFILE_MAX_ZOOM * TILE_SIZE;
  const mercator = new maplibregl.MercatorCoordinate(x / scale, y / scale, 0);
  const { lng, lat } = mercator.toLngLat();
  return [lng, lat];
}

/** Inverse of pixelToLngLat - used to turn a map click/drag position back into the
 * local-pixel (x, y) that gets sent to the backend. */
export function lngLatToPixel(lngLat: maplibregl.LngLatLike): [number, number] {
  const mercator = maplibregl.MercatorCoordinate.fromLngLat(lngLat);
  const scale = 2 ** RASTER_PROFILE_MAX_ZOOM * TILE_SIZE;
  return [mercator.x * scale, mercator.y * scale];
}
