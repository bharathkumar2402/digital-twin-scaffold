import { describe, expect, it } from "vitest";

import { parseLiveFrame } from "./liveMessages";

const alert = {
  asset_id: "a1",
  sensor_type: "torque",
  value: 61.5,
  z_score: 4.2,
  triggered_at: "2026-10-12T12:00:00Z",
};
const updated = {
  type: "scores_updated",
  facility_id: "f1",
  scored: 44,
  computed_at: "2026-10-12T12:00:00Z",
};

describe("parseLiveFrame", () => {
  it("reads an alert frame, which has no type field", () => {
    expect(parseLiveFrame(JSON.stringify(alert))).toEqual({ kind: "alert", alert });
  });

  it("reads an alert with a null z-score, and an explicitly typed alert", () => {
    expect(parseLiveFrame(JSON.stringify({ ...alert, z_score: null }))?.kind).toBe("alert");
    expect(parseLiveFrame(JSON.stringify({ ...alert, type: "alert" }))?.kind).toBe("alert");
  });

  it("reads scores_updated", () => {
    expect(parseLiveFrame(JSON.stringify(updated))).toEqual({
      kind: "scores_updated",
      facilityId: "f1",
      scored: 44,
      computedAt: "2026-10-12T12:00:00Z",
    });
  });

  it("does not carry unexpected fields of an alert through", () => {
    const parsed = parseLiveFrame(JSON.stringify({ ...alert, evil: "<script>" }));
    expect(parsed).toEqual({ kind: "alert", alert });
  });

  it.each([
    ["not json", "{nope"],
    ["empty string", ""],
    ["null", "null"],
    ["a string", '"hello"'],
    ["a number", "12"],
    ["an array", "[1,2]"],
    ["an empty object", "{}"],
    ["an unknown type that looks like an alert", JSON.stringify({ ...alert, type: "something_new" })],
    ["a scores_updated without a facility", JSON.stringify({ ...updated, facility_id: "" })],
    ["a scores_updated with a numeric facility", JSON.stringify({ ...updated, facility_id: 7 })],
    ["a scores_updated with a bad count", JSON.stringify({ ...updated, scored: "44" })],
    ["a scores_updated with an infinite count", '{"type":"scores_updated","facility_id":"f","scored":1e999,"computed_at":"x"}'],
    ["a scores_updated without a time", JSON.stringify({ ...updated, computed_at: undefined })],
    ["an alert without an asset", JSON.stringify({ ...alert, asset_id: undefined })],
    ["an alert with a text value", JSON.stringify({ ...alert, value: "61.5" })],
    ["an alert with a missing z-score field", JSON.stringify({ ...alert, z_score: undefined })],
  ])("drops %s", (_name, raw) => {
    expect(parseLiveFrame(raw)).toBeNull();
  });
});
