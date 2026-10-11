import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { EmptyState } from "../components/EmptyState";
import { Section } from "../components/Section";
import { ResultsSkeleton } from "../components/Skeleton";
import { StopCard } from "../components/StopCard";
import { useAssets } from "../hooks/useAssets";
import { useAuth } from "../hooks/useAuth";
import { useFacilities } from "../hooks/useFacilities";
import { useLatestPlan } from "../hooks/useLatestPlan";
import { canRunAnalyses } from "../lib/roles";
import { buildWorkPlan, formatDay, formatMinutes, groupLaterByDay, planAge } from "../lib/work";

// What to do today: the latest completed analysis turned into ordered stops per technician.
// Read-only for everyone. The roster is not linked to user accounts yet, so every technician's route
// is shown, grouped by name.
export function FacilityWorkPage(): React.JSX.Element {
  const { facilityId = "" } = useParams<{ facilityId: string }>();
  const { role } = useAuth();
  const facilities = useFacilities();
  const floors = facilities.data?.find((f) => f.id === facilityId)?.floors ?? [];
  const assetsQuery = useAssets(facilityId);
  const plan = useLatestPlan(facilityId);

  const assetsById = useMemo(
    () => new Map((assetsQuery.data ?? []).map((asset) => [asset.id, asset])),
    [assetsQuery.data]
  );
  const work = useMemo(
    () => (plan.run ? buildWorkPlan(plan.run.state_snapshot_json, assetsById) : null),
    [plan.run, assetsById]
  );
  const age = plan.run ? planAge(plan.run.created_at) : null;

  function content(): React.ReactNode {
    if (plan.isError || assetsQuery.isError) {
      return (
        <div role="alert" className="error-state">
          <span>Could not load the work plan.</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              plan.retry();
              void assetsQuery.refetch();
            }}
          >
            Retry
          </button>
        </div>
      );
    }
    if (plan.isPending || assetsQuery.isPending) return <ResultsSkeleton />;
    if (plan.none || work === null) {
      return (
        <EmptyState
          title="No work plan yet"
          action={
            canRunAnalyses(role) ? (
              <Link className="btn btn-sm" to={`/facilities/${facilityId}/analysis`}>
                Run an analysis
              </Link>
            ) : undefined
          }
        >
          {canRunAnalyses(role)
            ? "Run an analysis and the dispatched stops will appear here."
            : "A manager needs to run an analysis first. The dispatched stops will appear here."}
        </EmptyState>
      );
    }
    if (work.technicians.length === 0 && work.unassigned.length === 0 && work.later.length === 0) {
      return (
        <div className="all-clear" role="status">
          <strong>Nothing to do.</strong> The latest analysis scheduled no maintenance.
        </div>
      );
    }
    return (
      <>
        {work.technicians.map((tech) => (
          <Section key={tech.technicianId} title={tech.name}>
            <p className="scope-note">
              {tech.stops.length} {tech.stops.length === 1 ? "stop" : "stops"} &middot; about{" "}
              {formatMinutes(tech.totalMinutes)} in total
            </p>
            {tech.stops.length === 0 ? (
              <p style={{ fontSize: 13 }}>No stops assigned.</p>
            ) : (
              <ol className="stop-list">
                {tech.stops.map((stop) => (
                  <StopCard key={stop.taskId} facilityId={facilityId} stop={stop} floors={floors} />
                ))}
              </ol>
            )}
          </Section>
        ))}
        {work.unassigned.length > 0 && (
          <Section title="Not assigned">
            <p className="scope-note">
              These jobs are due for this shift but did not fit anyone&rsquo;s route (or no one has the
              skill). They will not happen unless someone takes them.
            </p>
            <ul>
              {work.unassigned.map((task) => (
                <li key={task.taskId} style={{ fontSize: 13 }}>
                  {task.assetName} ({task.priority} priority)
                </li>
              ))}
            </ul>
          </Section>
        )}
        {work.later.length > 0 && (
          <Section title="Scheduled for later">
            <details className="how-decided">
              <summary>
                {work.later.length} more {work.later.length === 1 ? "job" : "jobs"} on later days
                {work.later[0] ? ` (next: ${formatDay(work.later[0].scheduledDate)})` : ""}
              </summary>
              {groupLaterByDay(work.later).map((group) => (
                <p key={group.date} style={{ fontSize: 13, margin: "6px 0" }}>
                  <strong>{formatDay(group.date)}:</strong>{" "}
                  {group.tasks.map((t) => `${t.assetName} (${t.priority})`).join(", ")}
                </p>
              ))}
            </details>
          </Section>
        )}
      </>
    );
  }

  return (
    <AppShell facilityId={facilityId}>
      <div className="page">
        <div className="page-head">
          <h2>Work</h2>
          <p>The stops from the latest analysis, in the order to visit them.</p>
        </div>
        {plan.run && age && (
          <p className={age.stale ? "stale-plan" : "scope-note"} role={age.stale ? "alert" : undefined}>
            Plan from {new Date(plan.run.created_at).toLocaleString()}
            {work?.dispatchDate ? `, for the shift of ${formatDay(work.dispatchDate)}` : ""}.
            {age.stale && " This plan is more than a day old and may be out of date."}
          </p>
        )}
        {content()}
      </div>
    </AppShell>
  );
}
