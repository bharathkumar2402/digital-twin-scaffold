import { useParams } from "react-router-dom";

import { FacilityMap } from "../components/FacilityMap";
import { useFacilityMapUpload } from "../hooks/useFacilityMapUpload";

// No facilities-list endpoint exists on the backend yet (facility CRUD isn't a built
// task in PHASE_PLAN.md's Phase 2 - only the map-upload/status routes are), so this
// page is reached by direct URL with a known facility/upload id rather than through a
// facility picker. That's a real gap, but it's outside 2.5's scope: PHASE_PLAN.md's
// task list doesn't include a facilities-list route, and 2.4 confirmed tile rendering
// the same way - a direct URL hit, not a UI flow.
export function FacilityMapPage(): React.JSX.Element {
  const { facilityId, uploadId } = useParams<{ facilityId: string; uploadId: string }>();
  const { data, isPending, isError, error } = useFacilityMapUpload(facilityId ?? "", uploadId ?? "");

  if (!facilityId || !uploadId) {
    return <p>Missing facility or upload id in the URL.</p>;
  }
  if (isPending) {
    return <p>Loading upload status...</p>;
  }
  if (isError) {
    return <p role="alert">Failed to load upload status: {error.message}</p>;
  }
  if (data.status === "failed" || data.status === "conversion_failed") {
    return <p role="alert">Floor plan processing failed: {data.status}</p>;
  }
  if (data.status !== "tiled" || !data.tile_url_template) {
    return <p>Processing floor plan ({data.status})...</p>;
  }

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <FacilityMap tileUrlTemplate={data.tile_url_template} />
    </div>
  );
}
