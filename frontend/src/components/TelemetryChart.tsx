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

interface TelemetryChartProps {
  readings: TelemetryReading[];
}

export function TelemetryChart({ readings }: TelemetryChartProps): React.JSX.Element {
  if (readings.length === 0) {
    return <p>No telemetry recorded for this asset yet.</p>;
  }

  const { points, sensorTypes } = toChartData(readings);

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={points}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          dataKey="timestamp"
          tickFormatter={(value: string) => new Date(value).toLocaleTimeString()}
          minTickGap={30}
        />
        <YAxis />
        <Tooltip
          labelFormatter={(label) =>
            typeof label === "string" ? new Date(label).toLocaleString() : label
          }
        />
        <Legend />
        {sensorTypes.map((sensorType, index) => (
          <Line
            key={sensorType}
            type="monotone"
            dataKey={sensorType}
            stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
            dot={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
