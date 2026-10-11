import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./components/RequireAuth";
import { ResultsRedirect } from "./components/ResultsRedirect";
import { LoginPage } from "./pages/LoginPage";
import { FacilitiesPage } from "./pages/FacilitiesPage";
import { FacilityMapPage } from "./pages/FacilityMapPage";
import { AboutPage } from "./pages/AboutPage";
import { FacilityAnalysisPage } from "./pages/FacilityAnalysisPage";
import { FacilityAssetsPage } from "./pages/FacilityAssetsPage";
import { FacilityOverviewPage } from "./pages/FacilityOverviewPage";

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
          path="/facilities/:facilityId/overview"
          element={
            <RequireAuth>
              <FacilityOverviewPage />
            </RequireAuth>
          }
        />
        <Route
          path="/facilities/:facilityId/assets"
          element={
            <RequireAuth>
              <FacilityAssetsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/facilities/:facilityId/analysis"
          element={
            <RequireAuth>
              <FacilityAnalysisPage />
            </RequireAuth>
          }
        />
        <Route path="/facilities/:facilityId/results" element={<ResultsRedirect />} />
        <Route
          path="/about"
          element={
            <RequireAuth>
              <AboutPage />
            </RequireAuth>
          }
        />
        <Route
          path="/"
          element={
            <RequireAuth>
              <FacilitiesPage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
