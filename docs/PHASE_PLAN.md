# Phase-by-Phase Execution Plan

This is the operational companion to `PROJECT_PLAN.md`. That document explains **what**
we're building and **why** the scope is what it is. This document explains **how to
execute it week by week**, broken into Claude Code–sized sessions, with a clear
definition of done for every phase so no one has to guess whether a phase is finished.

Update the "Current phase" line in the root `CLAUDE.md` as you move through this.

**How to use this with Claude Code:**
1. Open a session, point it at this file and the relevant phase section.
2. Use plan mode first — have Claude propose a file/module breakdown for the session
   before it writes code. Approve or correct it, then let it build.
3. Keep sessions scoped to a single numbered task below, not a whole phase.
4. Commit at the end of every completed task, not just at the end of a session.
5. Don't move to the next phase until every item in that phase's "Definition of Done" is
   checked — partial phases compound into integration problems later (see Phase 7).

---

## Phase 0 — Team & Repo Setup (before Week 1, ~2–3 days)

**Goal:** Everyone can run the project locally and Claude Code has correct context before
anyone writes application code.

**Tasks**
1. `git init`, push the scaffold to a shared GitHub repo, protect `main` (PR required, at least 1 review).
2. Provision managed Postgres + TimescaleDB + Redis dev instances; fill in `infra/docker/.env` (never commit it).
3. Confirm `docker compose up` runs cleanly for every team member — this is a hard gate, don't proceed with a broken dev environment.
4. Set up GitHub Issues/Projects with one issue per numbered task in this document (Phases 1–7). This turns the plan into a literal tracker.
5. Agree on the team split (see `PROJECT_PLAN.md` §15) and assign phase ownership.
6. Each person runs Claude Code once against the repo and confirms it correctly summarizes the project back (a quick check that `CLAUDE.md` is being read).

**Definition of Done**
- [ ] Repo pushed, `main` protected
- [ ] Every team member has `docker compose up` working
- [ ] Managed DB/Redis credentials in place (not committed)
- [ ] Issue tracker populated from this document
- [ ] Team split assigned and written down

---

## Phase 1 — Foundation (Weeks 1–4)

**Goal:** Auth, tenancy, and the data ingest path work end to end. Nothing agent- or
map-related yet — this phase is the load-bearing wall everything else stands on.

**Claude Code sessions (in order)**

1. **Schema & migrations** — Design `tenants`, `users`, `facilities` tables (see `PROJECT_PLAN.md` §5). Write Alembic migration. Session should end with a migration that applies cleanly against the dev DB.
2. **Auth core** — JWT issuance/refresh (`python-jose`), password hashing, `/register`, `/login`, `/refresh` endpoints. Refresh token in HttpOnly cookie.
3. **Tenant context middleware** — Extract `tenant_id` from JWT, inject into a request-scoped dependency every route uses. Write the first cross-tenant test here as a template for the rest of the project (`backend/tests/cross_tenant/test_tenant_isolation_template.py`).
4. **RBAC** — Role enum (`superadmin | tenant_admin | facility_manager | technician | viewer`), permission-check dependency, applied to at least one protected route as a working example.
5. **Tenant management CRUD** — Create/list/update tenants (superadmin-only), create/list users within a tenant.
6. **TimescaleDB ingest endpoint** — `POST /telemetry`, Pydantic-validated payload, async bulk insert into a hypertable. Write the hypertable creation into the same migration set as task 1, or a follow-up migration.
7. **Simulated IoT data generator** — Standalone script producing realistic normal + anomalous sensor patterns, configurable rate, posts to the ingest endpoint.

**Definition of Done**
- [ ] A user can register, log in, get a JWT, and refresh it
- [ ] A second tenant's user cannot read the first tenant's data (cross-tenant test passes)
- [ ] RBAC blocks at least one route correctly, verified by a test
- [ ] Sensor data generator is running and rows are visibly landing in TimescaleDB
- [ ] `docker compose up` still works with no regressions from Phase 0

**Productivity note:** This phase is the best place to establish your PR review rhythm —
require the reviewer to actually run the cross-tenant test locally, not just read the diff.
Getting sloppy here compounds badly in Phase 5.

---

## Phase 2 — Map & Asset System (Weeks 5–9)

**Goal:** A facility floor plan can be uploaded safely, converted to tiles, and rendered
with clickable assets.

**Claude Code sessions (in order)**

1. **Upload endpoint + sandbox worker skeleton** — `POST /facilities/{id}/map` accepts SVG/DXF/PDF, hands off to `upload-sandbox` service (separate container, see `infra/docker/docker-compose.yml`). Build the isolation boundary first, before any real GDAL logic — confirm the sandbox worker genuinely cannot reach the main DB or filesystem.
2. **File sanitization** — Strip SVG active content, enforce size limits, reject malformed input before GDAL ever touches the file. Write adversarial test cases (malformed SVG with embedded script, oversized file, corrupt DXF) alongside this, not later.
3. **GDAL conversion pipeline** — Convert sanitized file to raster tiles, store in MinIO.
4. **Tile server wiring** — martin/TiTiler serving from MinIO; confirm a tile actually renders via a direct URL hit before touching the frontend.
5. **Frontend map integration** — MapLibre GL JS canvas, consumes the tile server, renders the uploaded facility.
6. **Asset CRUD + map placement** — Create/edit assets with `(x, y)` coordinates, drag-and-drop placement on the map.
7. **Asset dependency graph editor** — UI + backend for linking `asset_dependencies` (parent/child).
8. **Asset detail panel** — Click an asset → telemetry chart (from Phase 1's ingest data) + maintenance history stub.

**Definition of Done**
- [ ] A real floor plan (test with at least 2 different formats) uploads, processes, and renders in-browser
- [ ] Sandbox worker isolation is verified, not assumed — confirm it via the adversarial tests from task 2
- [ ] Assets can be placed, linked, and clicked for detail
- [ ] Coordinate system (local pixel/UTM) is documented in code comments where it's defined

**Productivity note:** Don't let map/tile debugging bleed into Phase 3 — if the coordinate
system or tile pipeline isn't solid by end of Phase 2, stop and fix it before building risk
visualization on top of a shaky map.

---

## Phase 3 — ML & Risk Engine (Weeks 10–13)

**Goal:** Assets show live, color-coded risk scores, and anomalies trigger real-time
alerts without flooding the system.

**Claude Code sessions (in order)**

1. **Feature engineering pipeline** — Pull rolling windows from TimescaleDB (30/90/365 day), build the feature set for the risk model.
2. **XGBoost training script** — Train offline on synthetic data with injected failure patterns; version the model file, store in MinIO.
3. **Risk inference service** — Celery task that loads the model and scores assets on demand.
4. **Anomaly detection** — Rolling Z-score check on live telemetry as it's ingested.
5. **Debounced alert triggering** — Implement the batching/cooldown logic from `PROJECT_PLAN.md` §7.1 *before* wiring it to anything downstream — this is a correctness-critical piece, write tests for the debounce window and per-asset cooldown independently of the rest of the pipeline.
6. **Real-time delivery** — Redis pub/sub → WebSocket server → frontend toast/alert.
7. **Risk visualization on the map** — Color-code asset markers green/yellow/red based on latest score.

**Definition of Done**
- [ ] Risk scores computed and visible on the map, color-coded
- [ ] A manually injected anomaly produces a browser alert in under 2 seconds
- [ ] Debounce logic verified by test: rapid repeated anomalies on one asset do NOT produce a flood of triggers
- [ ] Model version is recorded alongside each stored risk score (for auditability)

---

## Phase 4 — AI Agent System (Weeks 14–21) — the hardest phase, budget accordingly

**Goal:** The full 5-agent LangGraph pipeline runs end to end, every output validated,
producing a real maintenance schedule and answering "what if X fails?" queries.

**Build the safety net before the agents that need it — task 1 is not optional to defer.**

**Claude Code sessions (in order)**

1. **Pydantic output-validation layer** — Schemas for all 5 agent outputs (`app/schemas/agent_outputs/`), plus the shared retry-then-escalate logic (`app/agents/validation.py`) described in `PROJECT_PLAN.md` §4.4. Test this against deliberately malformed fake agent outputs before any real agent exists.
2. **`FacilityTwinState` + graph skeleton** — Wire an empty LangGraph with 5 placeholder nodes that just pass state through, validated at each hop. Confirm the graph runs end to end with dummy data before adding real logic to any node.
3. **Agent 1 — Planner** — Task decomposition + asset graph loading (tool call into Phase 2's asset tables). Validate output against its schema.
4. **Agent 2 — Risk Assessment** — Wraps Phase 3's inference service as an agent tool, adds LLM-driven prioritization/explanation on top of raw scores.
5. **Agent 3 — Maintenance & Inventory Planning** — Constraint-based scheduling + inventory tool call. This is the most logic-dense agent; consider a dedicated session just for the constraint solver before wiring it into the LangGraph node.
6. **Agent 4 — Route Optimization** — OR-Tools CVRP integration, 5-second solver time limit, distance matrix built from Phase 2's asset coordinates.
7. **Agent 5 — Simulation & Decision** — NetworkX cascade simulation over `asset_dependencies`, plus LLM synthesis into a final decision report with confidence score and human escalation path.
8. **Agent run logging + status UI hook** — Persist every run to `agent_runs` including validation failures; expose enough for the frontend's transparency panel (built in Phase 6).
9. **End-to-end integration test** — A single scripted scenario ("Pump 7 fails") that exercises all 5 agents and asserts on the final decision report shape.

**Definition of Done**
- [ ] Full pipeline runs end to end on a real scenario and produces a valid, schema-checked decision report
- [ ] A deliberately bad agent output (inject a malformed response) triggers retry-then-escalate, not a silent write
- [ ] "What if Pump 7 fails?" produces an impact report naming the correct downstream assets from the dependency graph
- [ ] Pipeline run completes within the 30-second SLO on a representative test facility size
- [ ] `agent_runs` table has a complete, queryable history of the test scenario

**Productivity note:** This phase has the most state-passing surface area in the project.
Resist the urge to build all 5 agents in parallel across team members before task 2's
skeleton is solid — a wrong state-shape decision made early is expensive to unwind once
three people are building on top of it. Get the skeleton right, then parallelize.

---

## Phase 5 — Multi-Tenancy & Hardening (Weeks 22–25)

**Goal:** Prove tenant isolation under adversarial testing, get one real service running
on k3s, and load-test the system.

**Claude Code sessions (in order)**

1. **RLS policy audit** — Go table by table against `PROJECT_PLAN.md` §5; confirm every `tenant_id` table has a policy AND a corresponding test in `backend/tests/cross_tenant/`. Close any gaps found during Phases 1–4.
2. **Rate limiting per tenant** — At the gateway (Traefik) level.
3. **RBAC enforcement audit** — Walk every route, confirm the correct role gate is applied; add UI-side gating to match.
4. **Upload pipeline fuzzing** — Extend Phase 2's adversarial tests with a proper fuzzing pass on the sandbox worker.
5. **k3s partial deployment** — Helm chart / manifests in `infra/k3s/` for the API, one Celery worker, and the frontend. Demonstrate autoscaling on at least one service under synthetic load.
6. **Load testing** — Locust scenario: 20–50 concurrent users across multiple tenants; feed results into Grafana dashboards.
7. **Observability wiring** — Prometheus + Grafana + Sentry fully connected, including the agent-validation-failure-rate panel.

**Definition of Done**
- [ ] Every `tenant_id` table has a passing cross-tenant isolation test
- [ ] Two browser sessions logged in as different tenants cannot see each other's data (manual demo-style check, not just automated tests)
- [ ] k3s deployment is real and running, autoscaling demonstrated live
- [ ] Load test results visible in Grafana, no unexplained error spikes at 20+ concurrent users
- [ ] Sentry captures a deliberately triggered error correctly

---

## Phase 6 — Frontend Polish & Documentation (Weeks 26–28)

**Goal:** The system is demo-ready and the documentation stands on its own without a
live narrator.

**Claude Code sessions (in order)**

1. **Executive dashboard** — KPIs: total assets, at-risk count, open maintenance items, inventory alerts.
2. **Maintenance schedule calendar view.**
3. **Simulation UI** — Select an asset, run "what if shutdown," view impact map overlay.
4. **Agent reasoning transparency panel** — Chain-of-thought + validation status per run, pulling from `agent_runs`.
5. **API documentation** — Confirm FastAPI's auto-generated OpenAPI docs are complete; add hand-written guides for anything non-obvious (auth flow, agent pipeline trigger conditions).
6. **Deployment guide** — Docker Compose instructions + k3s partial deployment instructions, cross-referenced with Appendix A's documented-only production design.
7. **Demo script + video** — Written walkthrough script covering every item in `PROJECT_PLAN.md` §14's checklist, then recorded.

**Definition of Done**
- [ ] Every checklist item in `PROJECT_PLAN.md` §14 is demonstrable
- [ ] A new team member (or evaluator) could follow the README + deployment guide and get the system running without asking a live question
- [ ] Demo video exists and covers all 8 checklist items in under ~10 minutes

---

## Phase 7 — Integration Buffer (Weeks 29–30)

**Goal:** No new features. Find and fix what breaks when everything runs together, and
rehearse.

**Tasks**
1. Full system run-through against the Phase 6 demo script, twice, by two different people.
2. Fix whatever the second run-through surfaces that the first one didn't.
3. Re-run the Phase 5 load test one more time against the final build.
4. Final pass on `agent_runs` and Grafana dashboards — confirm nothing regressed silently during Phase 6's frontend work.
5. Freeze `main` 48 hours before presentation; only critical-bug-fix commits after that.

**Definition of Done**
- [ ] Two full, independent dry runs of the demo completed without a hard failure
- [ ] `main` frozen with a tagged release commit before presentation day

---

## Quick reference: phase → owner mapping (fill in for your team)

| Phase | Primary owner | Supporting |
|---|---|---|
| 1 — Foundation | | |
| 2 — Map & Assets | | |
| 3 — ML & Risk | | |
| 4 — Agents | | |
| 5 — Multi-tenancy & Hardening | | |
| 6 — Frontend & Docs | | |
| 7 — Integration Buffer | everyone | everyone |
