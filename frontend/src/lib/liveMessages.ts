// What the server pushes over /ws/alerts, validated before anything acts on it. An anomaly alert
// carries no `type` field (it is the original frame, kept as it is); everything newer is typed.
// Anything else - a frame from a newer server, a malformed or hostile one - is dropped, never guessed at.
import type { AlertNotification } from "../types/api";

export type LiveMessage =
  | { kind: "alert"; alert: AlertNotification }
  | { kind: "scores_updated"; facilityId: string; scored: number; computedAt: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseLiveFrame(raw: string): LiveMessage | null {
  let frame: unknown;
  try {
    frame = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(frame)) return null;

  if (frame.type === "scores_updated") {
    if (typeof frame.facility_id !== "string" || frame.facility_id === "") return null;
    if (typeof frame.scored !== "number" || !Number.isFinite(frame.scored)) return null;
    if (typeof frame.computed_at !== "string") return null;
    return {
      kind: "scores_updated",
      facilityId: frame.facility_id,
      scored: frame.scored,
      computedAt: frame.computed_at,
    };
  }

  // An alert has no `type`; a frame with any other type is not an alert.
  if ("type" in frame && frame.type !== "alert") return null;
  if (
    typeof frame.asset_id !== "string" ||
    typeof frame.sensor_type !== "string" ||
    typeof frame.value !== "number" ||
    typeof frame.triggered_at !== "string" ||
    !(frame.z_score === null || typeof frame.z_score === "number")
  ) {
    return null;
  }
  return {
    kind: "alert",
    alert: {
      asset_id: frame.asset_id,
      sensor_type: frame.sensor_type,
      value: frame.value,
      z_score: frame.z_score,
      triggered_at: frame.triggered_at,
    },
  };
}
