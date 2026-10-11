import { useMemo } from "react";
import { useParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { AttentionList } from "../components/AttentionList";
import { FloorOverview } from "../components/FloorOverview";
import { HealthGate } from "../components/HealthGate";
import { KpiTiles } from "../components/KpiTiles";
import { Section } from "../components/Section";
import { useFacilityHealth } from "../hooks/useFacilityHealth";
import { summarizeHealth } from "../lib/assetHealth";
import { latestScoredAt, topAttention } from "../lib/attention";

// The landing page: is anything wrong, and where? Detail lives in the other tabs.
export function FacilityOverviewPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const health = useFacilityHealth(facilityId);
  const summary = useMemo(() => summarizeHealth(health.allRows), [health.allRows]);
  const attention = useMemo(() => topAttention(health.allRows, 5), [health.allRows]);
  const lastScoredAt = useMemo(() => latestScoredAt(health.allRows), [health.allRows]);

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h2>Overview</h2>
          <p>How the facility is doing right now, and where to look first.</p>
        </div>

        <HealthGate health={health} facilityId={facilityId}>
          <KpiTiles summary={summary} />
          <Section title="Needs attention">
            <AttentionList
              facilityId={facilityId}
              attention={attention}
              floors={health.facility?.floors ?? []}
              lastScoredAt={lastScoredAt}
              unscored={summary.unscored}
            />
          </Section>
          <Section title="Floors">
            <FloorOverview
              facilityId={facilityId}
              floors={health.floorHealth}
              totalAssets={health.allRows.length}
            />
          </Section>
        </HealthGate>
      </div>
    </AppShell>
  );
}
