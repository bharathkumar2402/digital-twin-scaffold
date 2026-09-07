# CLAUDE.md — AI Industrial Facility Digital Twin

This file is read automatically by Claude Code at the start of every session in this repo.
Read `docs/PROJECT_PLAN.md` for the full plan and `docs/PHASE_PLAN.md` for the
week-by-week, session-by-session execution runbook before starting any new phase of work.
`PHASE_PLAN.md` is the operational document — when starting a session, find the current
phase there, pick the next unchecked task, and use plan mode to confirm the approach
before writing code.

---

## What this project is

A multi-tenant web platform that creates a real-time digital replica of an industrial
facility (factory, warehouse, refinery, campus). Physical assets are nodes on a private
spatial map with live telemetry. A 5-agent LangGraph pipeline analyzes that data to
produce risk scores, maintenance schedules, technician routing, inventory alerts, and
"what-if" shutdown simulations.

Full architecture, phase plan, and rationale: `docs/PROJECT_PLAN.md`. Treat that document
as the source of truth for scope — if a request conflicts with it, flag the conflict
instead of silently picking one.

---

## Tech stack (do not substitute without discussion)

**Backend:** FastAPI (async, Python 3.11+), SQLAlchemy 2.x (async), Pydantic v2, Celery + Redis
**Agent layer:** LangGraph, Claude API (claude-sonnet-4-6 unless told otherwise)
**Databases:** Managed PostgreSQL + pgvector, managed TimescaleDB, managed Redis — NOT
  self-hosted HA in this repo. Self-hosted HA design lives only in
  `infra/appendix-a-future-prod/` as documentation, not runnable code.
**ML/Optimization:** scikit-learn / XGBoost (risk scoring), OR-Tools (CVRP routing), NetworkX (cascade simulation)
**Frontend:** React 18 + TypeScript, MapLibre GL JS, React Query, Recharts
**Infra:** Docker Compose (primary dev + demo environment), k3s (partial real deployment only —
  see plan doc §10.1), GitHub Actions, Prometheus + Grafana, Sentry

---

## Repo structure

```
backend/
  app/
    api/        — FastAPI routers, one file per resource (assets, tenants, alerts, etc.)
    agents/      — LangGraph nodes: planner, risk_assessment, maintenance_inventory,
                   route_optimization, simulation_decision. One file per agent.
    models/      — SQLAlchemy models
    schemas/     — Pydantic schemas — REQUEST schemas AND agent OUTPUT validation schemas
    services/    — business logic, tenant context, DB access
    core/        — config, auth/JWT, RLS session setup, celery app
  tests/
    unit/
    integration/       — testcontainers: real Postgres + Redis
    cross_tenant/       — RLS isolation attack tests — required for every new table

frontend/
  src/
    components/
    pages/
    hooks/
    lib/

infra/
  docker/                  — Docker Compose files (dev + demo)
  k3s/                     — real, deployable manifests for the partial demo deployment
  appendix-a-future-prod/  — DOCUMENTATION ONLY: full HA Postgres/Timescale/Redis,
                             full K8s namespace, SSO — described but not implemented here

docs/
  PROJECT_PLAN.md          — full plan, phases, risk table, source of truth for scope
```

---

## Non-negotiable engineering rules

1. **Every agent output is Pydantic-validated before it touches state or the DB.**
   No agent node returns raw dict/string data into `FacilityTwinState` without a schema
   check. On validation failure: retry once with the error appended to context, then halt
   that branch and log to `agent_runs` — never silently write unvalidated data.

2. **Every new table with a `tenant_id` column needs an RLS policy AND a cross-tenant test**
   in `backend/tests/cross_tenant/` before the PR is mergeable. No exceptions — this is the
   single highest-impact failure mode in the whole system.

3. **File uploads (floor plans) are processed only inside the sandboxed upload worker.**
   Never add a code path that runs GDAL/parsing directly in the main API process.

4. **Anomaly-triggered agent runs are debounced** (see plan §7.1) — never wire a raw sensor
   threshold breach directly to a full pipeline run. Batch over a window; use cooldowns per asset.

5. **Don't build the self-hosted HA data layer or full 12-service K8s namespace in this repo.**
   That's Appendix A — documentation only. If asked to "make this production-ready," check
   the plan doc first; it's an intentional, reasoned scope boundary, not an oversight.

6. **5 agents, not 8.** Asset-loading and inventory lookups are tool calls inside the
   Planner and Maintenance agents respectively, not separate LangGraph nodes. Don't
   re-split them without discussing — it was a deliberate consolidation to reduce
   state-passing failure surface.

7. **OR-Tools CVRP calls always set a solver time limit (5s default)** and return the
   best-found solution rather than blocking indefinitely.

---

## Working conventions

- Before starting a new phase, read the matching section of `docs/PROJECT_PLAN.md` §11 and
  propose a file/module breakdown before writing code (use plan mode for this).
- Keep sessions scoped to one deliverable at a time — one phase sub-task, not a whole phase.
- Commit at natural checkpoints, not just at the end of a session.
- Python: `ruff` + `mypy` clean before committing. TypeScript: `eslint` clean.
- Every new agent or API endpoint needs at least one test before the PR is considered done.
- If you (Claude) are unsure whether something is in scope for this repo vs. Appendix A,
  ask rather than building it — the scope boundary is deliberate and documented.

---

## Current phase

> Update this line as the team progresses — this tells Claude Code where you are without
> re-explaining it every session.

**Status:** Not started — Phase 0 (Team & Repo Setup) is next. See `docs/PHASE_PLAN.md`.
