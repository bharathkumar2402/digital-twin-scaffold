import { useMemo } from "react";
import { useParams } from "react-router-dom";

import { AgentAnalysisPanel } from "../components/AgentAnalysisPanel";
import { AppShell } from "../components/AppShell";
import { Section } from "../components/Section";
import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { canRunAnalyses } from "../lib/roles";

// What-if analyses and the five agents' reasoning. Running one is limited to managers and admins
// (the server enforces it); everyone who can open this page can read the history.
export function FacilityAnalysisPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const { role } = useAuth();
  const assetsQuery = useAssets(facilityId);
  const assetNames = useMemo(
    () => new Map((assetsQuery.data ?? []).map((asset) => [asset.id, asset.name])),
    [assetsQuery.data]
  );

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h2>Analysis</h2>
          <p>Ask &ldquo;what if this fails?&rdquo; and see how the five agents reached their answer.</p>
        </div>
        <Section title="Agent analysis">
          <p className="scope-note">Analyses cover the whole facility, not a single floor.</p>
          <AgentAnalysisPanel facilityId={facilityId} assetNames={assetNames} canRun={canRunAnalyses(role)} />
        </Section>
      </div>
    </AppShell>
  );
}
