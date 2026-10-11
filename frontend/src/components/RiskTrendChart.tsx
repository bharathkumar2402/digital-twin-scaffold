import { useState } from "react";

import { RISK_BAND_COLOR, RISK_BAND_TEXT_COLOR, riskBand } from "../lib/assetHealth";
import {
  BAND_EDGES,
  CHART,
  enteredHighRisk,
  formatAxisTime,
  isFlatSeries,
  layoutPoints,
  RUL_MIN_CHANGE,
  rulRange,
  yFor,
} from "../lib/riskChart";
import { describeTrend, trendOf, TREND_ARROW } from "../lib/riskTrend";
import type { RiskHistoryPoint } from "../types/api";

function pointLabel(point: RiskHistoryPoint): string {
  return `${new Date(point.computed_at).toLocaleString()}: risk ${point.score.toFixed(1)}`;
}

// How risk moved over the last few scores: shaded low / medium / high zones (the same bands as the
// map), a time-proportional line, labelled ends, a marker where it crossed into high risk, and a
// tooltip on every dot (hover or keyboard focus). Plain words underneath say what changed.
export function RiskTrendChart({ points }: { points: RiskHistoryPoint[] }): React.JSX.Element | null {
  const [active, setActive] = useState<number | null>(null);
  if (points.length === 0) return null;

  const laidOut = layoutPoints(points);
  const last = laidOut[laidOut.length - 1];

  if (points.length === 1) {
    return (
      <p style={{ fontSize: 13, margin: 0 }}>Only one score so far ({last.point.score.toFixed(1)}).</p>
    );
  }

  const first = laidOut[0];
  const spanHours =
    (new Date(last.point.computed_at).getTime() - new Date(first.point.computed_at).getTime()) /
    3_600_000;
  const crossing = enteredHighRisk(points);
  const crossingPoint = crossing ? laidOut[crossing.index] : null;
  const trend = trendOf(points);
  const rul = rulRange(points);
  const plotLeft = CHART.left;
  const plotRight = CHART.width - CHART.right;
  const zone = (from: number, to: number) => ({ y: yFor(to), height: yFor(from) - yFor(to) });
  const low = zone(0, BAND_EDGES.medium);
  const medium = zone(BAND_EDGES.medium, BAND_EDGES.high);
  const high = zone(BAND_EDGES.high, 100);
  const path = laidOut.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const summary = `Risk over the last ${points.length} scores: from ${first.point.score.toFixed(1)} to ${last.point.score.toFixed(1)}`;
  const tip = active === null ? null : laidOut[active];
  const tipText = tip ? pointLabel(tip.point) : "";
  const tipWidth = Math.min(CHART.width - 8, tipText.length * 5.4 + 12);
  const tipX = tip ? Math.min(Math.max(tip.x - tipWidth / 2, 4), CHART.width - tipWidth - 4) : 0;
  const tipY = tip ? (tip.y < 50 ? tip.y + 10 : tip.y - 26) : 0;

  // The chart is ONE tab stop. Arrow keys / Home / End move between its points (announced through the
  // live region below) and Escape lets go, so a keyboard user is not made to tab through every dot.
  function onKeyDown(event: React.KeyboardEvent<SVGSVGElement>): void {
    const last = laidOut.length - 1;
    const here = active ?? last;
    const next: Record<string, number | null> = {
      ArrowRight: Math.min(last, here + 1),
      ArrowLeft: Math.max(0, here - 1),
      Home: 0,
      End: last,
      Escape: null,
    };
    if (event.key in next) {
      event.preventDefault();
      setActive(next[event.key]);
    }
  }

  return (
    <div className="risk-chart">
      <svg
        role="group"
        aria-label={`${summary}. Use the left and right arrow keys to read each score.`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onFocus={() => setActive((current) => current ?? laidOut.length - 1)}
        onBlur={() => setActive(null)}
        viewBox={`0 0 ${CHART.width} ${CHART.height}`}
        width="100%"
        style={{ maxWidth: 420, display: "block" }}
      >
        <rect x={plotLeft} width={plotRight - plotLeft} {...low} fill={RISK_BAND_COLOR.low} opacity={0.1} />
        <rect x={plotLeft} width={plotRight - plotLeft} {...medium} fill={RISK_BAND_COLOR.medium} opacity={0.12} />
        <rect x={plotLeft} width={plotRight - plotLeft} {...high} fill={RISK_BAND_COLOR.high} opacity={0.12} />

        {[0, BAND_EDGES.medium, BAND_EDGES.high, 100].map((tick) => (
          <text key={tick} x={plotLeft - 4} y={yFor(tick) + 3} textAnchor="end" fontSize={9} fill="#64748b">
            {tick}
          </text>
        ))}

        {crossingPoint && (
          <g>
            <line
              x1={crossingPoint.x}
              x2={crossingPoint.x}
              y1={CHART.top}
              y2={CHART.height - CHART.bottom}
              stroke={RISK_BAND_COLOR.high}
              strokeDasharray="3 3"
              strokeWidth={1}
            />
            <text
              x={crossingPoint.x + (crossingPoint.x > CHART.width / 2 ? -3 : 3)}
              y={CHART.top + 8}
              textAnchor={crossingPoint.x > CHART.width / 2 ? "end" : "start"}
              fontSize={8}
              fill={RISK_BAND_COLOR.high}
            >
              entered high risk
            </text>
          </g>
        )}

        <polyline points={path} fill="none" stroke="#475569" strokeWidth={1.6} />

        {laidOut.map((p, i) => {
          const isLast = i === laidOut.length - 1;
          return (
            <circle
              key={p.point.computed_at + i}
              role="img"
              aria-label={pointLabel(p.point)}
              tabIndex={-1}
              cx={p.x}
              cy={p.y}
              r={isLast ? 4.5 : 3}
              fill={isLast ? RISK_BAND_COLOR[riskBand(p.point.score)] : "#fff"}
              stroke={isLast ? "#fff" : "#475569"}
              strokeWidth={isLast ? 1.5 : 1.4}
              style={{ cursor: "pointer", outline: "none" }}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
            />
          );
        })}

        <text x={first.x} y={first.y - 6} textAnchor="start" fontSize={9} fontWeight={600} fill="#334155">
          {first.point.score.toFixed(1)}
        </text>
        <text x={last.x} y={last.y - 8} textAnchor="end" fontSize={9} fontWeight={600} fill="#334155">
          {last.point.score.toFixed(1)}
        </text>

        <text x={first.x} y={CHART.height - 8} textAnchor="start" fontSize={9} fill="#64748b">
          {formatAxisTime(first.point.computed_at, spanHours)}
        </text>
        <text x={last.x} y={CHART.height - 8} textAnchor="end" fontSize={9} fill="#64748b">
          {formatAxisTime(last.point.computed_at, spanHours)}
        </text>

        {tip && (
          <g pointerEvents="none" data-testid="chart-tooltip">
            <rect x={tipX} y={tipY} width={tipWidth} height={16} rx={3} fill="#0f172a" />
            <text x={tipX + 6} y={tipY + 11} fontSize={9} fill="#fff">
              {tipText}
            </text>
          </g>
        )}
      </svg>

      <p className="sr-only" aria-live="polite" data-testid="chart-live">
        {tipText}
      </p>
      <p className="chart-legend">
        Zones: <span style={{ color: RISK_BAND_TEXT_COLOR.low }}>low</span> under {BAND_EDGES.medium},{" "}
        <span style={{ color: RISK_BAND_TEXT_COLOR.medium }}>medium</span> {BAND_EDGES.medium}&ndash;
        {BAND_EDGES.high - 1}, <span style={{ color: RISK_BAND_TEXT_COLOR.high }}>high</span>{" "}
        {BAND_EDGES.high} and above.
      </p>
      {isFlatSeries(points) ? (
        <p className="chart-caption">
          No change across the last {points.length} scores ({last.point.score.toFixed(1)}).
        </p>
      ) : (
        trend && (
          <p className={`trend trend-${trend.direction} chart-caption`}>
            {TREND_ARROW[trend.direction]} {describeTrend(trend)}
          </p>
        )
      )}
      {crossing && (
        <p className="chart-caption">
          Crossed into high risk (67+) on {new Date(crossing.at).toLocaleString()}.
        </p>
      )}
      {rul && Math.abs(rul.from - rul.to) >= RUL_MIN_CHANGE && (
        <p className="chart-caption">
          Remaining life: {rul.from.toFixed(1)} &rarr; {rul.to.toFixed(1)} cycles.
        </p>
      )}
    </div>
  );
}
