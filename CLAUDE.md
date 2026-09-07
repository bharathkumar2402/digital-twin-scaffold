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

## Standard task workflow — follow this automatically for every task, every time

This repo's work is tracked as GitHub issues numbered `<phase>.<task>` (e.g. `1.3`), each
mapping to one numbered task in `docs/PHASE_PLAN.md`. Whenever the user asks you to start,
continue, or move to a task — even with a short instruction like "next task" or "let's do
1.4" — run this exact sequence without being asked for each step individually:

1. **Resume check** (skip only if you just finished another task in this same session).
   Read `CLAUDE.md`'s "Current phase" line, check `git log` and `git status`, and report
   whether anything looks uncommitted or half-finished before proceeding.

2. **Plan first.** State which phase/task/issue you're doing and its name from
   `PHASE_PLAN.md`, then propose a file/module structure and approach. Do not write code
   yet — wait for explicit approval or a correction.

3. **Extra scrutiny check.** Before building, check whether this task is one of:
   - anything touching RLS policies or cross-tenant tests
   - Phase 4 tasks 1–2 (agent output validation layer, LangGraph skeleton)
   - Phase 2 tasks 1–2 (upload sandbox, file sanitization)
   - anything in Phase 5

   If so, explicitly explain your approach choice and list the adversarial/failure-case
   tests you'll write — not just the happy path — before building, and after building,
   walk through what each adversarial test actually checks rather than just reporting
   pass/fail.

4. **Build**, including tests as part of the same task, not a follow-up. Python: `ruff`
   + `mypy` clean. TypeScript: `eslint` clean.

5. **Verify before commit.** Run the tests and confirm they pass. Check the work against
   the "Non-negotiable engineering rules" above — flag anything that conflicts. Check it
   against the relevant phase's Definition of Done in `PHASE_PLAN.md` and say whether this
   task fully satisfies the relevant checklist items or only partially does.

6. **Commit and update tracker.** Commit referencing `Closes #<issue>` in the message.
   Update the "Current phase" line below to point at the next task, named exactly as it
   appears in `PHASE_PLAN.md`.

7. **Periodic check-in.** Every 5th task closed, or when a phase ends (whichever comes
   first), proactively run a full Definition of Done audit for the current phase against
   actual repo state — not against which issues are marked closed — and flag any gaps,
   even if the user didn't ask for this check.

Keep every session scoped to one task. If a request would span multiple tasks or phases,
say so and propose splitting it rather than doing it all in one pass. If you're unsure
whether something is in scope for this repo vs. `infra/appendix-a-future-prod/`, ask
rather than building it — that scope boundary is deliberate and documented in
`PROJECT_PLAN.md`.

---

## Current phase

> Update this line as the team progresses — this tells Claude Code where you are without
> re-explaining it every session.

**Status:** Phase 1 (Foundation) in progress — tasks 1 "Schema & migrations" (issue 1.1),
2 "Auth core" (issue 1.2), and 3 "Tenant context middleware" (issue 1.3) done. Next: task 4
"RBAC" (issue 1.4) — role enum, permission-check dependency, applied to at least one
protected route. See `docs/PHASE_PLAN.md`.

Note on 1.3: it also had to fix a real gap found while building it — the app's DB
connection was a superuser (Supabase's `postgres` role, which carries BYPASSRLS), so RLS
was never actually enforced for the app's own queries, only for the isolated test role
from 1.1. Migration 0002 adds a non-bypass `app_role` that `app/core/db.py` now connects
as; `infra/docker/.env` needs `APP_DB_USER`/`APP_DB_PASSWORD` set (see `.env.example`).
Phase 1's "second tenant's user cannot read the first tenant's data" DoD item is only
partially closed by this — proven for `users` via `GET /me`, not yet for a real
asset/facility-scoped route (that lands in tasks 1.5+).

Note: Phase 0's Definition of Done is not fully checked off yet (per project memory:
`docker compose up`, `.env` credentials, and team split are still open) — flagging this
per the resume-check step, not blocking on it since the user explicitly directed starting
Phase 1 task 1.