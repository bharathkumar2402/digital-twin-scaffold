import "@testing-library/jest-dom/vitest";

// jsdom has no URL.createObjectURL - maplibre-gl calls it unconditionally at module
// load time (to register its worker), which otherwise throws before any test using
// the real (unmocked) maplibre-gl module - e.g. src/lib/mapCoords.test.ts, which
// deliberately uses the real MercatorCoordinate math rather than a hand-rolled mock -
// even gets to run.
if (typeof window !== "undefined" && !window.URL.createObjectURL) {
  window.URL.createObjectURL = () => "blob:mock";
}
