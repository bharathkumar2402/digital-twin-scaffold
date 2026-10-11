import { useMemo } from "react";
import { useParams } from "react-router-dom";

import { AgentAnalysisPanel } from "../components/AgentAnalysisPanel";
import { AppShell } from "../components/AppShell";
import { Section } from "../components/Section";
import { useAuth } from "../hooks/useAuth";
import { useFacilityHealth } from "../hooks/useFacilityHealth";
import { suggestedQuestions } from "../lib/attention";
import { canRunAnalyses } from "../lib/roles";

// What-if analyses and the five agents' reasoning. Running one is limited to managers and admins
// (the server enforces it); everyone who can open this page can read the history.
export function FacilityAnalysisPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const { role } = useAuth();
  const health = useFacilityHealth(facilityId);
  const assetNames = useMemo(
    () => new Map([...health.assetsById].map(([id, asset]) => [id, asset.name])),
    [health.assetsById]
  );
  const suggestions = useMemo(() => suggestedQuestions(health.allRows, 3), [health.allRows]);

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h1>Analysis</h1>
          <p>Ask &ldquo;what if this fails?&rdquo; and see how the five agents reached their answer.</p>
        </div>
        <Section title="Agent analysis">
          <p className="scope-note">Analyses cover the whole facility, not a single floor.</p>
          <AgentAnalysisPanel
            facilityId={facilityId}
            assetNames={assetNames}
            canRun={canRunAnalyses(role)}
            suggestions={suggestions}
          />
        </Section>
      </div>
    </AppShell>
  );
}
