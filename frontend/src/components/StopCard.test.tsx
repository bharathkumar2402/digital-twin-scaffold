import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { PlanStop } from "../lib/work";
import type { Asset, FloorSummary } from "../types/api";
import { StopCard } from "./StopCard";

const pump: Asset = {
  id: "a1",
  facility_id: "f1",
  name: "Feed Pump",
  type: "pump",
  floor_level: 1,
  x: 0,
  y: 0,
  status: "operational",
  installed_date: null,
  manufacturer: null,
  model: null,
};

const floors: FloorSummary[] = [{ level: 1, name: "First", map_upload_id: "u1", asset_count: 1 }];

const stop = (over: Partial<PlanStop> = {}): PlanStop => ({
  stopNumber: 2,
  assetId: "a1",
  asset: pump,
  taskId: "t1",
  priority: "high",
  scheduledDate: "2026-10-12",
  arrivalMinutes: 75,
  serviceMinutes: 45,
  parts: [],
  ...over,
});

function renderStop(s: PlanStop, f: FloorSummary[] = floors) {
  render(
    <MemoryRouter>
      <ol>
        <StopCard facilityId="f1" stop={s} floors={f} />
      </ol>
    </MemoryRouter>
  );
}

describe("StopCard", () => {
  it("shows where, when and how long, in plain words", () => {
    renderStop(stop());
    const card = screen.getByRole("listitem", { name: "Stop 2: Feed Pump" });
    expect(within(card).getByText("L1")).toBeInTheDocument();
    expect(within(card).getByText("high priority")).toBeInTheDocument();
    expect(card).toHaveTextContent("About 1 h 15 min into the shift");
    expect(card).toHaveTextContent("45 min on site");
    expect(card).toHaveTextContent("due 2026-10-12");
  });

  it("lists parts to bring and calls out a shortage with the lead time", () => {
    renderStop(
      stop({
        parts: [
          { partName: "Bearing 6204", quantity: 2, shortage: null },
          {
            partName: "Seal Kit",
            quantity: 1,
            shortage: {
              part_id: "seal",
              part_name: "Seal Kit",
              needed_quantity: 1,
              available_quantity: 0,
              shortage_count: 1,
              lead_time_days: 5,
              impacted_asset_ids: ["a1"],
            },
          },
        ],
      })
    );
    expect(screen.getByText(/2 × Bearing 6204/)).toBeInTheDocument();
    expect(screen.getByText(/short: need 1, have 0, 5-day lead time/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("A part is short, so this job may have to wait.");
  });

  it("says nothing about shortages when all parts are in stock", () => {
    renderStop(stop({ parts: [{ partName: "Bearing", quantity: 1, shortage: null }] }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.queryByText(/short:/)).not.toBeInTheDocument();
  });

  it("links to the asset's details and its floor's map", () => {
    renderStop(stop());
    expect(screen.getByRole("link", { name: "Details" })).toHaveAttribute("href", "/facilities/f1/assets?asset=a1");
    expect(screen.getByRole("link", { name: "Show on map" })).toHaveAttribute(
      "href",
      "/facilities/f1/map/u1?asset=a1"
    );
  });

  it("hides Show on map when the floor has no map", () => {
    renderStop(stop(), []);
    expect(screen.queryByRole("link", { name: "Show on map" })).not.toBeInTheDocument();
  });

  it("handles an asset that no longer exists: no links, a clear name", () => {
    renderStop(stop({ asset: null, priority: null, scheduledDate: null }));
    expect(screen.getByText("Asset no longer in this facility")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
