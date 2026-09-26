import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  classifySeverity,
  computeStats,
  getSensorLabel,
  SEVERITY_COPY,
  summarizeBySensor,
  type Severity,
} from "../lib/sensorDisplay";
import type { TelemetryReading } from "../types/api";

const SERIES_COLORS = ["#1976d2", "#e65100", "#2e7d32", "#6a1b9a"];

interface ChartPoint {
  timestamp: string;
  [sensorType: string]: string | number;
}

function toChartData(readings: TelemetryReading[]): {
  points: ChartPoint[];
  sensorTypes: string[];
} {
  const sensorTypes = [...new Set(readings.map((r) => r.sensor_type))];
  const byTimestamp = new Map<string, ChartPoint>();

  // Oldest-first for the chart, but `readings` arrives newest-first from the API.
  for (const reading of [...readings].reverse()) {
    const point = byTimestamp.get(reading.timestamp) ?? { timestamp: reading.timestamp };
    point[reading.sensor_type] = reading.value;
    byTimestamp.set(reading.timestamp, point);
  }

  return { points: [...byTimestamp.values()], sensorTypes };
}

function formatValue(value: number): string {
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(1);
}

interface TelemetryChartProps {
  readings: TelemetryReading[];
}

export function TelemetryChart({ readings }: TelemetryChartProps): React.JSX.Element {
  if (readings.length === 0) {
    return <p style={{ fontSize: 13 }}>No telemetry recorded for this asset yet.</p>;
  }

  const { points, sensorTypes } = toChartData(readings);
  const summaries = summarizeBySensor(readings);
  const summaryBySensor = new Map(summaries.map((s) => [s.sensorType, s]));
  const worstSeverity = summaries.reduce<Severity>((worst, s) => {
    const rank: Record<Severity, number> = { unknown: 0, normal: 1, elevated: 2, critical: 3 };
    return rank[s.severity] > rank[worst] ? s.severity : worst;
  }, "unknown");

  return (
    <div>
      <p style={{ fontSize: 12, marginBottom: 10 }}>
        Latest reading per sensor, compared with this asset's own recent history.{" "}
        {worstSeverity === "critical" && (
          <strong style={{ color: "var(--color-danger)" }}>
            One or more readings are well outside the normal range.
          </strong>
        )}
        {worstSeverity === "elevated" && (
          <strong style={{ color: "var(--color-warning)" }}>
            One or more readings look unusual.
          </strong>
        )}
        {(worstSeverity === "normal" || worstSeverity === "unknown") &&
          "Everything currently looks within its normal range."}
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 8,
          marginBottom: 16,
        }}
      >
        {summaries.map((summary) => (
          <div
            key={summary.sensorType}
            className={`sensor-stat-card ${
              summary.severity === "critical"
                ? "severity-critical-border"
                : summary.severity === "elevated"
                  ? "severity-elevated-border"
                  : ""
            }`}
          >
            <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 2 }}>
              {summary.label}
            </div>
            <div style={{ fontSize: 17, fontWeight: 600, color: "var(--color-text)" }}>
              {formatValue(summary.latestValue)}
              <span style={{ fontSize: 12, fontWeight: 400, color: "var(--color-text-muted)" }}>
                {" "}
                {summary.unit}
              </span>
            </div>
            <span className={`status-badge severity-${summary.severity}`} style={{ marginTop: 4 }}>
              <span className="status-dot" />
              {SEVERITY_COPY[summary.severity]}
            </span>
          </div>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={points}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(value: string) => new Date(value).toLocaleTimeString()}
            minTickGap={30}
            tick={{ fontSize: 11 }}
          />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={(label) =>
              typeof label === "string" ? new Date(label).toLocaleString() : label
            }
            formatter={(value, name) => [
              `${formatValue(Number(value))} ${summaryBySensor.get(String(name))?.unit ?? ""}`,
              getSensorLabel(String(name)),
            ]}
          />
          <Legend formatter={(name: string) => getSensorLabel(name)} wrapperStyle={{ fontSize: 12 }} />
          {sensorTypes.map((sensorType, index) => {
            const summary = summaryBySensor.get(sensorType);
            const values = points
              .map((p) => p[sensorType])
              .filter((v): v is number => typeof v === "number");
            const stats = computeStats(values);
            return (
              <Line
                key={sensorType}
                type="monotone"
                dataKey={sensorType}
                name={sensorType}
                stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
                strokeWidth={summary?.severity === "critical" ? 2.5 : 1.5}
                dot={(props: { cx?: number; cy?: number; value?: number; index?: number }) => {
                  const { cx, cy, value, index } = props;
                  const key = `${sensorType}-dot-${index}`;
                  if (cx === undefined || cy === undefined || value === undefined) {
                    return <g key={key} />;
                  }
                  const severity = classifySeverity(value, stats);
                  if (severity !== "critical" && severity !== "elevated") {
                    return <g key={key} />;
                  }
                  return (
                    <circle
                      key={key}
                      cx={cx}
                      cy={cy}
                      r={4}
                      fill={severity === "critical" ? "var(--color-danger)" : "var(--color-warning)"}
                      stroke="#fff"
                      strokeWidth={1}
                    />
                  );
                }}
                connectNulls
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
