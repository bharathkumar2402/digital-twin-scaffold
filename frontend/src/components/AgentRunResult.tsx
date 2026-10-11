import { replaceAssetIds } from "../lib/assetNames";
import type { AgentRunDetail } from "../types/api";

interface AgentRunResultProps {
  run: AgentRunDetail;
  // asset id -> name, from the facility's real asset list.
  assetNames: Map<string, string>;
}

const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  halted_for_escalation: "Halted for escalation",
  failed: "Failed",
};

function statusPill(status: string): string {
  if (status === "completed") return "pill-ok";
  if (status === "halted_for_escalation") return "pill-medium";
  return "pill-alarm";
}

function makeNameOf(assetNames: Map<string, string>) {
  // An id that is not in the facility is shown as such (never crashes, never hidden): the
  // agents are supposed to use only real assets, so this is worth a human's attention.
  return (assetId: string): string =>
    assetNames.get(assetId) ?? `Unknown asset (${assetId.slice(0, 8)}…)`;
}

function StageCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="stage-card" aria-label={title}>
      <h4>{title}</h4>
      {children}
    </section>
  );
}

const muted = { fontSize: 13 } as const;

export function AgentRunResult({ run, assetNames }: AgentRunResultProps): React.JSX.Element {
  const state = run.state_snapshot_json;
  const nameOf = makeNameOf(assetNames);
  const halted = run.status !== "completed";
  const risks = state.risk_scores;
  const items = state.maintenance_schedule;
  const gaps = state.inventory_gaps;
  const routes = state.dispatch_routes;
  const cascade = state.simulation_result;

  return (
    <div>
      {halted && (
        <div className="banner-warning" role="alert">
          <strong>The pipeline stopped before finishing.</strong>{" "}
          {state.escalation_reason
            ? replaceAssetIds(state.escalation_reason, assetNames)
            : "No reason was recorded."}
          {state.errors && state.errors.length > 0 && (
            <ul style={{ margin: "6px 0 0 18px", padding: 0 }}>
              {state.errors.map((error, i) => (
                <li key={i}>{replaceAssetIds(error, assetNames)}</li>
              ))}
            </ul>
          )}
          <div style={{ marginTop: 6 }}>Stages that completed before the stop are under &ldquo;How this was decided&rdquo;.</div>
        </div>
      )}

      <section className="answer-card" aria-label="Answer">
        <h4>Answer</h4>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 10 }}>
          <span className={`pill ${statusPill(run.status)}`}>
            {STATUS_LABEL[run.status] ?? run.status}
          </span>
          <span style={muted}>{(run.duration_ms / 1000).toFixed(1)} s</span>
          <span style={muted}>{new Date(run.created_at).toLocaleString()}</span>
          {/* A halted run keeps the confidence of the last agent that set one; it says nothing
              about a run that did not finish, so it is only shown for completed runs. */}
          {!halted && run.confidence !== null && (
            <span style={muted}>confidence {(run.confidence * 100).toFixed(0)}%</span>
          )}
          {state.user_query && <span style={muted}>query: &ldquo;{state.user_query}&rdquo;</span>}
        </div>

        {state.user_query && cascade && (
          <div className="impact" role="group" aria-label="What-if impact">
            <p style={{ margin: "0 0 6px", fontSize: 14 }}>
              If <strong>{nameOf(cascade.root_cause_asset_id)}</strong> fails:
            </p>
            <div className="impact-tiles">
              <div>
                <div className="impact-value">{cascade.total_affected_assets}</div>
                <div className="impact-label">Assets affected</div>
              </div>
              <div>
                <div className="impact-value">{cascade.cascade_depth}</div>
                <div className="impact-label">Cascade levels</div>
              </div>
              <div>
                <div className="impact-value">{cascade.estimated_downtime_hours} h</div>
                <div className="impact-label">Estimated downtime</div>
              </div>
            </div>
            <p style={{ margin: "6px 0 0", fontSize: 12 }}>
              Downtime is a rule-of-thumb estimate over the cascade, not a prediction.
            </p>
          </div>
        )}

        {run.decision_report ? (
          <p className="answer-summary">{replaceAssetIds(run.decision_report, assetNames)}</p>
        ) : (
          <p style={muted}>No summary was produced.</p>
        )}
      </section>

      <StageCard title="Validation errors">
        {run.validation_errors_json.length === 0 ? (
          <p style={muted}>None &ndash; every agent output passed its schema check.</p>
        ) : (
          <ul style={{ margin: "0 0 0 18px", padding: 0, fontSize: 13 }}>
            {run.validation_errors_json.map((error, i) => (
              <li key={i} style={{ color: "var(--color-danger)" }}>
                {error}
              </li>
            ))}
          </ul>
        )}
      </StageCard>

      <details className="how-decided">
        <summary>How this was decided: the four agent steps</summary>
      <StageCard title="1. Risk priorities">
        <p style={{ fontSize: 12 }}>
          The score and RUL come from the trained models. The &ldquo;why&rdquo; text is a
          rule-based explanation over recent telemetry, not the model&rsquo;s own reasoning.
        </p>
        {!risks || risks.length === 0 ? (
          <p style={muted}>Not produced.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Asset</th>
                <th>Tier</th>
                <th>Score</th>
                <th>RUL</th>
                <th>Why / recommended action</th>
              </tr>
            </thead>
            <tbody>
              {[...risks]
                .sort((a, b) => a.urgency_rank - b.urgency_rank)
                .map((risk) => (
                  <tr key={risk.asset_id}>
                    <td>{risk.urgency_rank}</td>
                    <td>{nameOf(risk.asset_id)}</td>
                    <td>
                      <span className={`pill pill-${risk.risk_tier}`}>{risk.risk_tier}</span>
                    </td>
                    <td>{risk.risk_score.toFixed(1)}</td>
                    <td>{risk.rul_cycles === null ? "—" : risk.rul_cycles.toFixed(1)}</td>
                    <td>
                      {risk.primary_risk_factors.join("; ")}
                      <div style={{ color: "var(--color-text-muted)" }}>{risk.recommended_action}</div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </StageCard>

      <StageCard title="2. Maintenance schedule and inventory">
        {!items || items.length === 0 ? (
          <p style={muted}>Not produced.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Asset</th>
                <th>Priority</th>
                <th>Hours</th>
                <th>Skills</th>
                <th>Parts</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.item_id}>
                  <td>{item.scheduled_date}</td>
                  <td>{nameOf(item.asset_id)}</td>
                  <td>
                    <span className={`pill pill-${item.priority}`}>{item.priority}</span>
                  </td>
                  <td>{item.estimated_duration_hours}</td>
                  <td>{item.required_technician_skills.join(", ")}</td>
                  <td>
                    {item.required_parts.map((p) => `${p.quantity}× ${p.part_name}`).join(", ") ||
                      "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {gaps && gaps.length > 0 ? (
          <div className="banner-warning" style={{ marginTop: 10 }}>
            <strong>Inventory shortages</strong>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              {gaps.map((gap) => (
                <li key={gap.part_id}>
                  {gap.part_name}: need {gap.needed_quantity}, have {gap.available_quantity}{" "}
                  (lead time {gap.lead_time_days} d) &ndash; affects{" "}
                  {gap.impacted_asset_ids.map(nameOf).join(", ")}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          items &&
          items.length > 0 && <p style={{ ...muted, marginTop: 8 }}>No inventory shortages.</p>
        )}
      </StageCard>

      <StageCard title="3. Technician routes">
        {!routes || routes.length === 0 ? (
          <p style={muted}>Not produced.</p>
        ) : (
          routes.map((route) => (
            <div key={route.technician_id} style={{ marginBottom: 8, fontSize: 13 }}>
              <strong>{route.technician_name}</strong> &ndash;{" "}
              {route.total_route_duration_minutes.toFixed(0)} min (
              {route.total_service_minutes.toFixed(0)} service,{" "}
              {route.total_travel_minutes.toFixed(0)} travel)
              <ol style={{ margin: "4px 0 0 18px", padding: 0 }}>
                {route.assigned_stops.map((stop) => (
                  <li key={stop.task_id}>
                    {nameOf(stop.asset_id)} &ndash; arrive +
                    {stop.estimated_arrival_minutes.toFixed(0)} min, service{" "}
                    {stop.service_duration_minutes.toFixed(0)} min
                  </li>
                ))}
              </ol>
            </div>
          ))
        )}
      </StageCard>

      <StageCard title="4. Failure cascade (what-if)">
        {!cascade ? (
          <p style={muted}>Not produced.</p>
        ) : (
          <div style={{ fontSize: 13 }}>
            <div>
              If <strong>{nameOf(cascade.root_cause_asset_id)}</strong> fails,{" "}
              {cascade.total_affected_assets} asset(s) are affected over {cascade.cascade_depth}{" "}
              cascade level(s).
            </div>
            <div>
              Directly affected:{" "}
              {cascade.directly_affected_asset_ids.map(nameOf).join(", ") || "none"}
            </div>
            <div>
              Downstream shutoff:{" "}
              {cascade.downstream_shutoff_asset_ids.map(nameOf).join(", ") || "none"}
            </div>
            <div>
              Estimated downtime: {cascade.estimated_downtime_hours} h{" "}
              <span style={{ color: "var(--color-text-muted)" }}>
                (a rule-of-thumb heuristic over the cascade, not a prediction)
              </span>
            </div>
          </div>
        )}
      </StageCard>

      </details>
    </div>
  );
}
