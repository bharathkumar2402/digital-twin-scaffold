import { Link } from "react-router-dom";

import { floorLabel } from "../lib/floors";
import { assetDetailHref, assetMapHref } from "../lib/mapLinks";
import { formatMinutes, type PlanStop } from "../lib/work";
import type { FloorSummary } from "../types/api";

const PRIORITY_PILL: Record<string, string> = {
  critical: "pill-high",
  high: "pill-high",
  medium: "pill-medium",
  low: "pill-low",
};

// One visit on a technician's route: where, when, what, and what to bring.
export function StopCard({
  facilityId,
  stop,
  floors,
}: {
  facilityId: string;
  stop: PlanStop;
  floors: FloorSummary[];
}): React.JSX.Element {
  const name = stop.asset?.name ?? "Asset no longer in this facility";
  const mapHref = stop.asset ? assetMapHref(facilityId, floors, stop.asset) : null;
  const shortParts = stop.parts.filter((part) => part.shortage !== null);

  return (
    <li className="stop-card" aria-label={`Stop ${stop.stopNumber}: ${name}`}>
      <div className="stop-number" aria-hidden="true">
        {stop.stopNumber}
      </div>
      <div className="stop-body">
        <div className="attention-head">
          <strong>{name}</strong>
          {stop.asset && <span className="attention-floor">{floorLabel(stop.asset.floor_level)}</span>}
          {stop.priority && <span className={`pill ${PRIORITY_PILL[stop.priority]}`}>{stop.priority} priority</span>}
        </div>
        <p className="stop-when">
          About {formatMinutes(stop.arrivalMinutes)} into the shift &middot; {formatMinutes(stop.serviceMinutes)} on site
          {stop.scheduledDate ? ` · due ${stop.scheduledDate}` : ""}
        </p>

        {stop.parts.length > 0 && (
          <div className="stop-parts">
            <strong>Bring:</strong>
            <ul>
              {stop.parts.map((part) => (
                <li key={part.partName}>
                  {part.quantity} &times; {part.partName}
                  {part.shortage && (
                    <span className="stop-short">
                      {" "}
                      &mdash; short: need {part.shortage.needed_quantity}, have {part.shortage.available_quantity}
                      {part.shortage.lead_time_days > 0 ? `, ${part.shortage.lead_time_days}-day lead time` : ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
            {shortParts.length > 0 && (
              <p className="stop-short" role="note">
                {shortParts.length === 1 ? "A part is" : `${shortParts.length} parts are`} short, so this job may
                have to wait.
              </p>
            )}
          </div>
        )}

        {stop.asset && (
          <div className="attention-links">
            <Link className="btn btn-sm" to={assetDetailHref(facilityId, stop.asset)}>
              Details
            </Link>
            {mapHref && (
              <Link className="btn btn-sm btn-ghost" to={mapHref}>
                Show on map
              </Link>
            )}
          </div>
        )}
      </div>
    </li>
  );
}
