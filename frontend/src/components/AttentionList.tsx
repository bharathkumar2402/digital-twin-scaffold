import { Link } from "react-router-dom";

import { assetStatus } from "../lib/assetStatus";
import type { Attention } from "../lib/attention";
import { floorLabel } from "../lib/floors";
import { describeTrend, trendOf, TREND_ARROW } from "../lib/riskTrend";
import { assetDetailHref, assetMapHref } from "../lib/mapLinks";
import type { FloorSummary, RiskHistoryPoint } from "../types/api";
import { StatusBadge } from "./StatusBadge";

interface AttentionListProps {
  facilityId: string;
  attention: Attention;
  floors: FloorSummary[];
  // When the newest score was computed; shown in the all-clear state.
  lastScoredAt: string | null;
  // Assets the risk model has not scored: they are not "fine", they are unknown.
  unscored: number;
  // Recent scores per asset id (oldest first). Optional: cards are complete without them.
  history?: Map<string, RiskHistoryPoint[]>;
}

// The few machines to look at first, each with why and what to do. Replaces "scan a 44-row table".
export function AttentionList({
  facilityId,
  attention,
  floors,
  lastScoredAt,
  unscored,
  history,
}: AttentionListProps): React.JSX.Element {
  if (attention.total === 0) {
    return (
      <div className="all-clear" role="status">
        <strong>All clear.</strong> No machine needs attention right now
        {lastScoredAt ? ` (last scored ${new Date(lastScoredAt).toLocaleString()}).` : "."}
        {unscored > 0 &&
          ` ${unscored} ${unscored === 1 ? "asset is" : "assets are"} not scored, so ${
            unscored === 1 ? "it was" : "they were"
          } not assessed.`}
      </div>
    );
  }

  return (
    <>
      <ul className="attention-list">
        {attention.top.map((row) => {
          const status = assetStatus(row);
          const mapHref = assetMapHref(facilityId, floors, row.asset);
          const trend = trendOf(history?.get(row.asset.id) ?? []);
          return (
            <li key={row.asset.id} className={`attention-card attention-${status.key}`} aria-label={row.asset.name}>
              <div className="attention-head">
                <strong>{row.asset.name}</strong>
                <span className="attention-floor">{floorLabel(row.asset.floor_level)}</span>
                <StatusBadge status={status} />
              </div>
              <p className="attention-why">{status.reasons.join("; ")}.</p>
              {trend && trend.direction !== "flat" && (
                <p className={`trend trend-${trend.direction}`}>
                  {TREND_ARROW[trend.direction]} {describeTrend(trend)}
                </p>
              )}
              <p className="attention-action">{status.action}.</p>
              <div className="attention-links">
                <Link className="btn btn-sm" to={assetDetailHref(facilityId, row.asset)}>
                  Details
                </Link>
                {mapHref && (
                  <Link className="btn btn-sm btn-ghost" to={mapHref}>
                    Show on map
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="scope-note">
        Showing {attention.top.length} of {attention.total}.{" "}
        <Link to={`/facilities/${facilityId}/assets?show=attention`}>
          See all {attention.total} needing attention
        </Link>
      </p>
    </>
  );
}
