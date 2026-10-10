import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./components/RequireAuth";
import { LoginPage } from "./pages/LoginPage";
import { FacilityMapPage } from "./pages/FacilityMapPage";
import { FacilityResultsPage } from "./pages/FacilityResultsPage";

export function App(): React.JSX.Element {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/facilities/:facilityId/map/:uploadId"
          element={
            <RequireAuth>
              <FacilityMapPage />
            </RequireAuth>
          }
        />
        <Route
          path="/facilities/:facilityId/results"
          element={
            <RequireAuth>
              <FacilityResultsPage />
            </RequireAuth>
          }
        />
        <Route path="/" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
