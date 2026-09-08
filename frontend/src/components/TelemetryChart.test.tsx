import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import type { TelemetryReading } from "../types/api";
import { TelemetryChart } from "./TelemetryChart";

// Recharts' ResponsiveContainer measures itself via ResizeObserver, which jsdom
// doesn't implement - without a stub, some recharts versions throw when the global is
// missing entirely.
beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    class ResizeObserverStub implements ResizeObserver {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    globalThis.ResizeObserver = ResizeObserverStub;
  }
  // jsdom never lays out elements, so ResponsiveContainer measures 0x0 via
  // getBoundingClientRect and recharts renders nothing - stub a real-looking size so
  // the chart's children actually mount.
  HTMLElement.prototype.getBoundingClientRect = () =>
    ({
      width: 400,
      height: 220,
      top: 0,
      left: 0,
      bottom: 220,
      right: 400,
      x: 0,
      y: 0,
      toJSON() {},
    }) as DOMRect;
});

const readings: TelemetryReading[] = [
  { sensor_type: "temperature", value: 40, unit: "celsius", timestamp: "2026-01-01T00:00:00Z" },
  { sensor_type: "temperature", value: 42, unit: "celsius", timestamp: "2026-01-01T01:00:00Z" },
  { sensor_type: "vibration", value: 0.2, unit: "g", timestamp: "2026-01-01T01:00:00Z" },
];

describe("TelemetryChart", () => {
  it("shows an empty-state message when there are no readings", () => {
    render(<TelemetryChart readings={[]} />);
    expect(screen.getByText(/no telemetry recorded/i)).toBeInTheDocument();
  });

  it("renders a legend entry per sensor type present in the readings", () => {
    render(<TelemetryChart readings={readings} />);
    expect(screen.getByText("temperature")).toBeInTheDocument();
    expect(screen.getByText("vibration")).toBeInTheDocument();
  });
});
