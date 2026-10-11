import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { AgentAnalysisPanel } from "../components/AgentAnalysisPanel";
import { AppShell } from "../components/AppShell";
import { AssetDetailDrawer } from "../components/AssetDetailDrawer";
import { AssetHealthTable } from "../components/AssetHealthTable";
import { KpiTiles } from "../components/KpiTiles";
import { ModelCards } from "../components/ModelCards";
import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { useRiskScores } from "../hooks/useRiskScores";
import { buildHealthRows, summarizeHealth } from "../lib/assetHealth";

// Mirrors the backend's RUN_ROLES in app/api/agent_runs.py; the server still enforces it.
const RUN_ROLES = new Set(["superadmin", "tenant_admin", "facility_manager"]);

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="results-section" aria-label={title}>
      <h3 style={{ marginBottom: 10 }}>{title}</h3>
      {children}
    </section>
  );
}

export function FacilityResultsPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const { role } = useAuth();
  const canRun = role !== null && RUN_ROLES.has(role);

  const assetsQuery = useAssets(facilityId);
  const scoresQuery = useRiskScores(facilityId);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  const rows = useMemo(
    () => buildHealthRows(assetsQuery.data ?? [], scoresQuery.data ?? []),
    [assetsQuery.data, scoresQuery.data]
  );
  const assetNames = useMemo(
    () => new Map((assetsQuery.data ?? []).map((asset) => [asset.id, asset.name])),
    [assetsQuery.data]
  );
  const summary = useMemo(() => summarizeHealth(rows), [rows]);
  const selectedRow = rows.find((row) => row.asset.id === selectedAssetId) ?? null;

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h2>Facility results</h2>
          <p>What the risk, remaining-life and vibration models and the five agents say right now.</p>
        </div>

        {assetsQuery.data && scoresQuery.data && <KpiTiles summary={summary} />}

      <Section title="Asset health">
        {(assetsQuery.isPending || scoresQuery.isPending) && <p style={{ fontSize: 13 }}>Loading...</p>}
        {(assetsQuery.isError || scoresQuery.isError) && (
          <p role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>
            Could not load this facility&rsquo;s assets or scores.
          </p>
        )}
        {assetsQuery.data && scoresQuery.data && (
          <>
            <AssetHealthTable
              rows={rows}
              selectedAssetId={selectedAssetId}
              onSelect={setSelectedAssetId}
            />
            {selectedRow && (
              <AssetDetailDrawer
                facilityId={facilityId}
                row={selectedRow}
                onClose={() => setSelectedAssetId(null)}
              />
            )}
          </>
        )}
      </Section>

      <Section title="Agent analysis">
        <AgentAnalysisPanel facilityId={facilityId} assetNames={assetNames} canRun={canRun} />
      </Section>

      <Section title="About these models">
        <ModelCards />
      </Section>
      </div>
    </AppShell>
  );
}
