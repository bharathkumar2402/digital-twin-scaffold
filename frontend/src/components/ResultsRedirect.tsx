import { Navigate, useLocation, useParams } from "react-router-dom";

// The old single Results page was split into tabs. Keep old links working: a floor filter belongs
// to the Assets tab (query string kept), anything else lands on the Overview.
export function ResultsRedirect(): React.JSX.Element {
  const { facilityId } = useParams<{ facilityId: string }>();
  const { search } = useLocation();
  const hasFloor = new URLSearchParams(search).has("floor");
  return (
    <Navigate
      to={hasFloor ? `/facilities/${facilityId}/assets${search}` : `/facilities/${facilityId}/overview`}
      replace
    />
  );
}
