import type { TelemetryReading } from "../types/api";

// Human-readable labels for the sensor types the demo generator (and any real
// ingest client) sends - see backend/scripts/iot_data_generator.py's SENSOR_PROFILES.
// Anything not listed here falls back to a title-cased version of the raw key so an
// unrecognized sensor type still renders reasonably instead of breaking.
const SENSOR_LABELS: Record<string, string> = {
  temperature_c: "Temperature",
  pressure_kpa: "Pressure",
  vibration_mm_s: "Vibration",
  humidity_pct: "Humidity",
};

export function getSensorLabel(sensorType: string): string {
  return (
    SENSOR_LABELS[sensorType] ??
    sensorType
      .replace(/_(c|pct|kpa|mm_s)$/i, "")
      .split("_")
      .filter(Boolean)
      .map((word) => word[0].toUpperCase() + word.slice(1))
      .join(" ")
  );
}

export type Severity = "normal" | "elevated" | "critical" | "unknown";

export const SEVERITY_COPY: Record<Severity, string> = {
  normal: "Normal",
  elevated: "Unusual",
  critical: "Critical",
  unknown: "Not enough data",
};

// Not the same computation as the backend's live rolling-Z-score anomaly detector
// (app/services/anomaly_detection_service.py) - this is a simpler client-side read
// of "how far is the latest value from this sensor's own recent readings", purely for
// giving a non-expert viewer a quick normal/unusual/critical cue in the sidebar. The
// authoritative anomaly signal remains the WebSocket-driven alert toast.
const MIN_SAMPLES_FOR_STATS = 5;
const ELEVATED_Z = 2;
const CRITICAL_Z = 3;

export interface SensorSeriesStats {
  mean: number;
  stdDev: number;
}

export function computeStats(values: number[]): SensorSeriesStats | null {
  if (values.length < MIN_SAMPLES_FOR_STATS) {
    return null;
  }
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / values.length;
  return { mean, stdDev: Math.sqrt(variance) };
}

export function classifySeverity(value: number, stats: SensorSeriesStats | null): Severity {
  if (!stats || stats.stdDev === 0) {
    return stats ? "normal" : "unknown";
  }
  const z = Math.abs((value - stats.mean) / stats.stdDev);
  if (z >= CRITICAL_Z) return "critical";
  if (z >= ELEVATED_Z) return "elevated";
  return "normal";
}

export interface SensorSummary {
  sensorType: string;
  label: string;
  latestValue: number;
  unit: string;
  latestTimestamp: string;
  severity: Severity;
  history: TelemetryReading[]; // oldest-first
}

// `readings` arrives newest-first from the API (see TelemetryChart's toChartData
// comment) - grouping preserves that order, so history[0] per sensor is also its
// latest reading, which doubles as the "current value" card without a second pass.
export function summarizeBySensor(readings: TelemetryReading[]): SensorSummary[] {
  const bySensor = new Map<string, TelemetryReading[]>();
  for (const reading of readings) {
    const list = bySensor.get(reading.sensor_type);
    if (list) {
      list.push(reading);
    } else {
      bySensor.set(reading.sensor_type, [reading]);
    }
  }

  return [...bySensor.entries()].map(([sensorType, newestFirst]) => {
    const values = newestFirst.map((r) => r.value);
    // Latest value's own stats come from the rest of its history, not including
    // itself, so a single spike can't dilute the baseline it's being measured against.
    const stats = computeStats(values.slice(1));
    const latest = newestFirst[0];
    return {
      sensorType,
      label: getSensorLabel(sensorType),
      latestValue: latest.value,
      unit: latest.unit,
      latestTimestamp: latest.timestamp,
      severity: classifySeverity(latest.value, stats),
      history: [...newestFirst].reverse(),
    };
  });
}
