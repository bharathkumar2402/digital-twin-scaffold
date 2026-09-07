# AI Industrial Facility Digital Twin
## Professional Project Plan — Revised, Right-Sized Edition
### Version 2.0 | September 2026

---

> **Audience:** Engineering faculty review, capstone committee, industry evaluators
> **Verdict on complexity:** This is a genuine 5–7 month senior/graduate-level project for a solo or two-person team. It touches distributed systems, real-time data pipelines, multi-agent AI orchestration, geospatial engineering, simulation, and multi-tenant SaaS architecture. Version 2.0 keeps every component that makes this technically hard, and removes the operational complexity (self-managed HA databases, full production Kubernetes) that adds risk without adding learning value for a capstone timeline.
>
> **What changed from v1.0:** Agent count reduced from 8 to 5 (consolidation, not deletion of capability). Data layer moved to managed services for the build/demo, with self-hosted HA documented as a future-work appendix. Kubernetes scope reduced to a partial real deployment plus a documented full-scale plan. Added file-upload sandboxing and agent output validation, both of which were gaps in v1.0. Market-sizing citations removed pending verification — the complexity argument stands on technical merits alone. Timeline includes an explicit integration/buffer phase.

---

## 1. PROJECT OVERVIEW

### What Are We Actually Building?

A **multi-tenant web platform** that creates a living digital replica of an industrial facility — factory, warehouse, oil refinery, or campus. Every physical asset (pump, conveyor, HVAC unit, transformer, robot arm) is represented as a node on a private spatial map with real-time telemetry. A coordinated set of AI agents continuously monitors, analyzes, and acts on that data to answer the questions managers actually need answered:

- Which assets are about to fail, and how confident are we?
- What is the optimal maintenance schedule for the next 30 days?
- If Sector B shuts down tonight, what downstream processes break?
- Where should Technician 4 go right now, and in what order?
- Do we have the right spare parts on hand for next week's predicted failures?

This is a **stateful, agent-driven simulation and decision platform** operating on private geospatial data in near-real-time — not a dashboard, and not a CRUD app with AI sprinkled on top.

---

## 2. WHY THIS IS GENUINELY COMPLEX

| Complexity Layer | Why It Is Hard |
|---|---|
| Multi-agent orchestration | 5 agents must coordinate state without deadlocking, looping, or hallucinating decisions |
| Real-time telemetry pipeline | Sensor data arrives continuously; system must ingest, validate, store, and react in near-real-time |
| Private geospatial map | No Google Maps. You must build or integrate a tile server for facility floor plans with custom coordinate systems |
| Predictive ML models | Risk scores require training on time-series sensor data with domain-specific feature engineering |
| Simulation engine | "What if Sector B goes down?" requires a graph-based dependency model with cascade simulation |
| Route optimization | Technician dispatch is an NP-hard vehicle routing problem variant |
| Multi-tenant isolation | Multiple companies on one platform with strict data separation, enforced and tested at the database level |
| Untrusted file ingestion | Facility floor plans are user-uploaded SVG/DXF/PDF files that must be parsed and rasterized safely |
| Agent output reliability | LLM-driven agents must be schema-validated so a hallucinated output can't reach a maintenance schedule unchecked |

This is a hard problem independent of market size — the technical breakdown above is the actual argument. (v1.0's market-sizing citations have been removed pending verification against a primary source; if you want them reinstated, verify against Precedence Research, Gartner, or MarketsandMarkets directly and cite the specific report and page.)

---

## 3. SYSTEM ARCHITECTURE

### 3.1 High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                             │
│   React + TypeScript SPA   |   Responsive web (mobile-friendly) │
│   MapLibre GL (map canvas) |   Role-based dashboards            │
└───────────────────┬─────────────────────────────────────────────┘
                    │ HTTPS / WebSocket (wss://)
┌───────────────────▼─────────────────────────────────────────────┐
│                      API GATEWAY LAYER                          │
│            Nginx (reverse proxy + SSL termination)              │
│            Traefik (rate limiting, auth middleware)             │
└───────────────────┬─────────────────────────────────────────────┘
                    │
        ┌───────────┴────────────┐
        │                        │
┌───────▼────────┐    ┌──────────▼──────────┐
│  REST API      │    │  WebSocket Server    │
│  FastAPI       │    │  FastAPI + Redis     │
│  (Async)       │    │  Pub/Sub             │
└───────┬────────┘    └──────────┬───────────┘
        │                        │
┌───────▼────────────────────────▼───────────┐
│              CORE SERVICES LAYER            │
│  Auth Service  |  Tenant Service            │
│  Asset Service |  Telemetry Ingest Service  │
│  Notification  |  File/Map Service          │
│  (File uploads processed in an isolated,    │
│   no-shell-access sandbox worker)           │
└───────┬─────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────┐
│           AI AGENT ORCHESTRATION LAYER        │
│              LangGraph (State Machine)        │
│                                               │
│  Planner/Asset Loader → Risk Assessor         │
│  → Maintenance & Inventory Planner            │
│  → Route Optimizer → Simulation Agent         │
│  → Decision Agent                             │
│  (every agent output is Pydantic-validated    │
│   before being written to state or the DB)    │
└───────┬──────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────┐
│              DATA LAYER                       │
│  Managed PostgreSQL + pgvector (structured)   │
│  Managed TimescaleDB (time-series sensor)     │
│  Managed Redis (cache + pub/sub)              │
│  Self-hosted MinIO (maps, files, models)      │
│  Celery + Redis Broker  (async task queue)    │
└──────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────┐
│           INFRASTRUCTURE LAYER                │
│  Docker Compose (dev + demo environment)      │
│  k3s partial deployment (2–3 stateless        │
│   services, proves K8s competency)            │
│  GitHub Actions CI/CD                         │
│  Prometheus + Grafana (observability)         │
│  Sentry (error tracking)                      │
└──────────────────────────────────────────────┘
```

### 3.2 Technology Stack (Revised)

**Backend**
- **FastAPI (Python 3.11+)** — async-native, auto-generates OpenAPI docs
- **LangGraph** — graph-based multi-agent orchestration with built-in state checkpointing and per-node timeouts
- **Celery + Redis** — async task queue for long-running agent jobs (risk scoring, simulation runs, route optimization)
- **SQLAlchemy 2.x (async)** — ORM with connection pooling
- **Pydantic** — request validation AND agent-output validation (new in v2.0 — every agent node's return value is validated against a schema before it's trusted downstream)

**Databases — now managed services for build/demo**
- **Managed PostgreSQL + pgvector** (e.g., a hosted Postgres provider) — structured data; avoids the multi-week effort of self-managing HA Postgres, which was a scope trap in v1.0
- **Managed TimescaleDB** (Timescale Cloud, or a single self-hosted instance if a managed tier isn't available) — time-series sensor data, compression, time-bucket queries
- **Managed Redis** — caching, WebSocket pub/sub, Celery broker
- *Self-hosted HA versions of all three (primary + replica, StatefulSets) are documented in Appendix A as future production work, not built for the capstone.*

**Frontend** — unchanged from v1.0
- React 18 + TypeScript, MapLibre GL JS, Recharts/D3.js, React Query

**Infrastructure — reduced scope**
- **Docker Compose** for local development and the live demo — this is the primary way the system runs
- **k3s** (lightweight Kubernetes) for a *partial* real deployment: API service, one Celery worker, and the frontend, deployed and demonstrated live. This proves K8s competency without requiring you to correctly operate 12 StatefulSets under a deadline.
- **GitHub Actions** — CI/CD pipeline (lint → test → build → deploy)
- **Prometheus + Grafana** — metrics, dashboards, alerting
- **MinIO** — self-hosted S3-compatible store for facility map tiles and ML models (this one component is fine to self-host; it's stateless-ish and low-risk)

**ML/AI** — unchanged
- scikit-learn / XGBoost for offline-trained risk models
- Claude API for agent reasoning and natural-language summaries
- OR-Tools for route optimization
- NetworkX for the dependency graph and cascade simulation

---

## 4. THE AI AGENT SYSTEM (REVISED: 5 AGENTS, NOT 8)

### 4.1 Framework Choice: LangGraph

Unchanged reasoning from v1.0: the agents are stateful, cyclical, and interdependent — risk scores feed maintenance plans, maintenance plans constrain routing, routing depends on inventory. LangGraph's state-machine model with conditional branching and persistent state is the right fit; CrewAI is faster to prototype but breaks down at this level of state dependency.

### 4.2 Why 5 agents instead of 8

Two of v1.0's agents were doing deterministic lookups, not independent reasoning, and didn't need their own LLM-driven node:

- **Asset Mapping Agent → folded into the Planner** as a plain function/tool call. Loading a tenant's asset graph from PostgreSQL and building a NetworkX graph doesn't benefit from LLM reasoning — it's a query and a graph constructor. Making it a full agent node just adds an extra state hop and another point of failure for no benefit.
- **Inventory Agent → folded into the Maintenance Planning Agent** as a tool. Cross-referencing a maintenance schedule against a parts table and flagging shortages is a lookup with threshold rules, not a decision that needs its own reasoning step. The Maintenance Planner calls it as a tool and incorporates the result into its output.

This keeps every capability from v1.0 — nothing is cut, just consolidated into fewer, more capable nodes. It also directly reduces your biggest schedule risk: state-passing bugs between agents, which scale roughly with the *number of edges* in the graph, not just the node count.

### 4.3 Agent Definitions

**Agent 1 — Planner Agent** *(absorbs Asset Mapping)*
- Role: Receives user intent or scheduled trigger. Loads the tenant's asset graph (query + NetworkX construction, as a tool call, not a separate agent). Decomposes the goal into a sequence of sub-tasks and decides which downstream agents to activate.
- Tools: Task decomposition prompt, tenant context fetcher, PostgreSQL asset query, GeoJSON/graph constructor, agent router
- Output: Structured execution plan + asset graph (JSON) — **Pydantic-validated**

**Agent 2 — Risk Assessment Agent**
- Role: For each asset, generates a risk score (0–100) using the ML model. Considers last maintenance date, sensor anomaly history, asset age, failure rate for that class, and adjacent asset failures.
- Tools: TimescaleDB telemetry query (last 30/90/365 days), XGBoost model inference, anomaly detection (rolling Z-score)
- Output: Risk-scored asset list ranked by urgency — **Pydantic-validated**

**Agent 3 — Maintenance & Inventory Planning Agent** *(absorbs Inventory)*
- Role: Takes the risk-scored list and generates a 30-day maintenance schedule, respecting technician availability, regulatory maintenance windows, and asset interdependencies. As part of the same pass, cross-references the schedule against spare parts inventory (tool call) and flags shortages / drafts POs for parts needed in the next 14 days.
- Tools: Calendar API, constraint solver, asset dependency graph, inventory table query, lead-time database
- Output: Maintenance schedule + inventory status report (JSON) — **Pydantic-validated**

**Agent 4 — Route Optimization Agent**
- Role: Given the day's maintenance tasks and available technicians, computes optimal dispatch routes (Capacitated Vehicle Routing Problem variant).
- Tools: OR-Tools CVRP solver (5-second time limit, return best-found solution), facility distance matrix builder, technician skill matcher
- Output: Per-technician ordered task list with estimated travel times — **Pydantic-validated**

**Agent 5 — Simulation & Decision Agent** *(Simulation and Decision combined at the reasoning level, but kept as two logical passes within one node to control LLM call count)*
- Role: Runs "what-if" scenarios (e.g., "What happens if Boiler Unit 3 goes offline?") by propagating shutdown through the dependency graph via NetworkX, then synthesizes the outputs of all upstream agents into a final natural-language executive summary with a confidence score, escalating critical decisions to a human operator.
- Tools: NetworkX cascade simulator, production flow model, LLM summarization, confidence scorer, alert publisher (Redis pub/sub → WebSocket → UI)
- Output: Impact report + decision report + real-time alerts — **Pydantic-validated**

> **Note on Agent 5:** merging Simulation and Decision into one node is a judgment call to reduce total LLM round-trips per pipeline run (cost and latency both matter under the 30-second SLO). If your evaluator wants to see them as clearly separate reasoning steps, they can remain two nodes in the graph while sharing this description — the important thing for grading purposes is that the *simulation logic* (graph cascade) and the *synthesis/escalation logic* (LLM summary + confidence) are implemented as distinct, testable functions, whether or not they're two LangGraph nodes.

### 4.4 Agent Output Validation (New in v2.0)

Every agent's return value is checked against a Pydantic schema before it is written into `FacilityTwinState` or persisted to the database. If validation fails:

1. The agent is retried once with an error message appended to its prompt context.
2. If it fails again, the pipeline halts that branch, logs the failure to `agent_runs`, and the Decision Agent surfaces it to a human operator rather than silently propagating bad data (e.g., a maintenance task for an asset ID that doesn't exist in the tenant's graph).

This directly answers the question a good faculty reviewer will ask: *"How do you know the LLM didn't just hallucinate a decision?"*

### 4.5 Agent State Schema (Simplified)

```python
class FacilityTwinState(TypedDict):
    tenant_id: str
    trigger: str                    # "scheduled" | "user_query" | "alert"
    user_query: Optional[str]
    asset_graph: Optional[dict]     # NetworkX serialized
    risk_scores: Optional[list]
    maintenance_schedule: Optional[list]
    inventory_gaps: Optional[list]
    dispatch_routes: Optional[list]
    simulation_result: Optional[dict]
    decision_report: Optional[str]
    confidence: Optional[float]
    validation_errors: list[str]    # new: agent-output validation failures
    errors: list[str]
    iteration_count: int            # loop guard
```

---

## 5. DATA MODELS (KEY TABLES) — unchanged from v1.0

```
tenants              — id, name, plan_tier, created_at
users                — id, tenant_id, email, role, hashed_password
facilities           — id, tenant_id, name, map_file_ref, bounds_geojson
assets               — id, facility_id, name, type, coordinates (x,y),
                       installed_date, manufacturer, model, status
asset_dependencies   — parent_asset_id, child_asset_id, dependency_type
sensor_readings      — asset_id, sensor_type, value, unit, timestamp (TimescaleDB hypertable)
risk_scores          — asset_id, score, computed_at, model_version, factors_json
maintenance_records  — asset_id, performed_by, performed_at, type, notes, parts_used
maintenance_schedule — id, asset_id, planned_date, assigned_technician_id, priority, status
technicians          — id, tenant_id, name, skills[], current_location (x,y), shift
inventory            — id, tenant_id, part_number, name, quantity, reorder_threshold
alerts               — id, tenant_id, asset_id, severity, message, acknowledged_at
agent_runs           — id, tenant_id, trigger, state_snapshot_json, duration_ms, status,
                       validation_errors_json
```

---

## 6. PRIVATE GEOSPATIAL MAP SYSTEM (with sandboxing added)

No Google Maps, no government data — the facility map is a private floor plan uploaded by the client, which is also why it needs to be treated as untrusted input.

**Implementation approach:**

1. Client uploads facility floor plan as SVG, DXF, or PDF.
2. **The file is processed in an isolated worker with no shell access to the rest of the system**, a hard file-size limit, and SVG active-content (scripts, external entity references) stripped before any parsing happens. This runs as a separate container/process from the main API — a compromised parser can't reach the database or other tenants' data. *(This was a gap in v1.0: accepting arbitrary user-uploaded SVG/DXF/PDF and running it straight through GDAL/MapTiler without isolation is a real attack surface — SVG XML entity injection and DXF/PDF parser vulnerabilities have a documented CVE history.)*
3. Backend converts the sanitized file to raster tile format using GDAL/MapTiler.
4. Tiles are stored in MinIO and served via a self-hosted tile server (martin or TiTiler).
5. MapLibre GL JS on the frontend consumes these tiles.
6. Assets are overlaid as GeoJSON point layers with custom icons by type.
7. Coordinates are in a local pixel or UTM coordinate system (not WGS84).
8. Users can click assets on the map to see their telemetry, risk score, and maintenance history.

**Why this is hard:** arbitrary facility layouts, different DXF versions, coordinate system transformations, safe handling of untrusted files, and serving tiles with correct caching headers is a full sub-project on its own.

---

## 7. REAL-TIME TELEMETRY PIPELINE — unchanged

```
[Simulated IoT Sensors]
        │
        ▼  (MQTT or HTTP POST)
[Ingest Service — FastAPI endpoint]
        │
        ├── Validate schema (Pydantic)
        ├── Write to TimescaleDB (async bulk insert)
        ├── Check anomaly thresholds
        │       └── If threshold exceeded → publish to Redis channel
        │                   └── WebSocket server broadcasts to connected clients
        └── Trigger async Celery task for risk re-scoring (debounced — see §7.1)
```

IoT sensors are simulated with a configurable data generator (normal operation + injected anomalies), which is standard practice for academic digital twin projects. Real sensor integration (OPC-UA, MQTT brokers) is documented as production extension work.

### 7.1 Debouncing agent triggers (New in v2.0)

v1.0 didn't fully reason through what happens if the full pipeline re-runs on every single anomaly event across hundreds of simulated sensors — that's both an LLM cost risk and a latency risk against the 30-second SLO. v2.0 adds: anomaly events are batched over a short window (e.g., 10–30 seconds) before triggering a pipeline run, and repeated anomalies on the same asset within a cooldown period don't re-trigger a full run — only a lighter-weight risk re-score. The full 5-agent pipeline runs on a schedule and on explicit user queries ("what if..."), not on every individual sensor blip.

---

## 8. MULTI-TENANCY DESIGN — unchanged, still core to the project

- **Row-Level Security (RLS) in PostgreSQL** — every table has `tenant_id`; RLS policies ensure queries only return matching rows
- **Tenant context middleware in FastAPI** — JWT contains `tenant_id`, injected into every DB query
- **Redis key namespacing** — all keys prefixed `tenant:{id}:`
- **MinIO path prefixing per tenant** *(simplified from separate buckets per tenant in v1.0 — same isolation guarantee, less provisioning overhead)*
- **Rate limiting per tenant** at the API gateway level

RLS policies are tested with explicit automated cross-tenant attack scenarios that run on every CI build — this stays in scope because it's both technically important and the single highest-impact failure mode if skipped.

---

## 9. AUTHENTICATION & AUTHORIZATION (reduced scope)

- JWT (access token 15 min, refresh token 7 days) via `python-jose`
- Role-based access control (RBAC): `superadmin | tenant_admin | facility_manager | technician | viewer`
- Each role has different UI access and API endpoint permissions
- All tokens rotated on refresh; refresh token stored in HttpOnly cookie
- Audit log for all write operations
- **OAuth2/SSO (Google/Microsoft) is deferred** — cut from the core build for solo/2-person teams (see §15). Documented as a near-term extension rather than built and demoed, since it adds real integration work without teaching anything the JWT/RBAC system doesn't already cover for grading purposes.

---

## 10. DEPLOYMENT ARCHITECTURE (Reduced Scope)

### 10.1 What actually runs where

| Environment | What it's for | What runs |
|---|---|---|
| **Docker Compose** | Local dev + the live demo | Everything: API, WS server, Celery workers, agent runner, frontend, MinIO, tile server, Prometheus/Grafana. Managed Postgres/TimescaleDB/Redis connected via env vars. |
| **k3s (partial)** | Proving K8s competency | API service, one Celery worker, frontend — deployed with real manifests/Helm chart, autoscaling demonstrated on at least one service. |
| **Documented, not built** | Appendix A | Full StatefulSet layout for self-hosted HA Postgres/TimescaleDB/Redis, full 12-service K8s namespace, multi-node cluster — written up as the production target with a clear "here's what we'd change and why" narrative. |

This gets you a real, defensible deployment story — you can demo the full system running via Compose, and separately demo a real k3s deployment with autoscaling, without betting your grade on correctly operating a dozen StatefulSets under deadline pressure.

### 10.2 CI/CD Pipeline (GitHub Actions) — unchanged

```
Push to feature branch
    → Lint (ruff, mypy, eslint)
    → Unit tests (pytest, vitest)
    → Integration tests (testcontainers: real Postgres + Redis)
    → Build Docker images
    → Push to container registry (GHCR)

Merge to main
    → All above +
    → Build production images
    → Deploy to staging (Compose or k3s namespace)
    → Run smoke tests against staging
    → Manual approval gate
    → Deploy to production namespace (rolling update)
```

### 10.3 Observability Stack — unchanged

- **Prometheus** — scrapes FastAPI (`/metrics` via `prometheus-fastapi-instrumentator`), Celery, DB exporter, Redis
- **Grafana** — dashboards: API latency, agent run duration, risk score distribution, WebSocket connections, Celery queue depth, DB query times, **agent validation-failure rate (new)**
- **Sentry** — application error tracking
- **Structured logging** — JSON logs, shipped to a log aggregator (Loki)

**Key SLOs:**
- API p95 latency < 200ms
- Agent full pipeline run < 30 seconds
- WebSocket event delivery < 500ms
- Uptime > 99.5% (demo-period target, not a claim about the managed backing services)

---

## 11. DEVELOPMENT PHASES (Revised: ~28–30 weeks with buffer)

### Phase 1 — Foundation (Weeks 1–4)
- Project setup: monorepo, Docker Compose dev environment, GitHub Actions skeleton
- Managed Postgres + TimescaleDB provisioned; database schema and migrations (Alembic)
- Auth system: registration, login, JWT, RBAC
- Tenant management CRUD
- Basic FastAPI structure: routers, dependencies, error handlers
- Sensor reading ingest endpoint
- Simulated IoT data generator

**Deliverable:** Working auth system, data flowing into TimescaleDB, basic asset CRUD

### Phase 2 — Map & Asset System (Weeks 5–9, +1 week vs v1.0)
- Facility floor plan upload with **sandboxed processing worker** (new)
- GDAL conversion pipeline, MinIO integration, martin tile server setup
- MapLibre GL JS frontend integration
- Asset CRUD with map placement
- Asset dependency graph editor
- Asset detail panel (telemetry chart, maintenance history)

**Deliverable:** Interactive private facility map with assets placed and linked, upload pipeline sandboxed and tested against malformed files

### Phase 3 — ML & Risk Engine (Weeks 10–13)
- Feature engineering pipeline from TimescaleDB sensor data
- XGBoost risk scoring model (synthetic data, injected failure patterns)
- Model versioning in MinIO
- Async risk score computation via Celery
- Risk score visualization on the map (color-coded)
- Anomaly detection + **debounced** alert triggering (new, see §7.1)
- Real-time alerts via Redis pub/sub → WebSocket → UI

**Deliverable:** Assets color-coded by risk; alerts fire on threshold breach without flooding the agent pipeline

### Phase 4 — AI Agent System (Weeks 14–21, 8 weeks — was 6 in v1.0)
- LangGraph installation and state machine setup
- **Pydantic output-validation layer** built first, before agents (new — build the safety net before the agents that need it)
- Agent 1 (Planner, absorbs asset loading)
- Agent 2 (Risk Assessment)
- Agent 3 (Maintenance & Inventory Planning)
- Agent 4 (Route Optimization — OR-Tools CVRP)
- Agent 5 (Simulation & Decision)
- Agent run logging to `agent_runs`, including validation failures
- Agent status UI (shows which agent is running, what it returned, any validation retries)

**Deliverable:** Full 5-agent pipeline runnable end-to-end with output validation. "What if Pump 7 fails?" produces a full impact report.

### Phase 5 — Multi-Tenancy & Hardening (Weeks 22–25)
- PostgreSQL RLS policies, with automated cross-tenant tests in CI
- Rate limiting per tenant
- Full RBAC enforcement and UI gating by role
- MinIO path-prefix tenant isolation
- Load testing (Locust): 20–50 concurrent users, multiple tenants simultaneously
- k3s partial deployment (API, one worker, frontend) with Helm chart and autoscaling demo
- Prometheus + Grafana + Sentry integration
- Security pass: SQL injection, IDOR, JWT attacks, **upload-pipeline fuzzing** (new)

**Deliverable:** Platform handles multiple tenants simultaneously with verifiable isolation; one service demonstrably running and autoscaling on real k3s

### Phase 6 — Frontend Polish & Documentation (Weeks 26–28)
- Executive dashboard (KPIs: assets, at-risk count, open maintenance, inventory alerts)
- Maintenance schedule calendar view
- Simulation UI ("what if shutdown" → impact map)
- Agent reasoning transparency panel (chain of thought + validation status)
- API documentation (auto-generated + hand-written guides)
- Deployment guide (Compose + k3s), Appendix A (full production HA design, documented not built)
- Demo video and final presentation

**Deliverable:** Demo-ready platform with clear documentation of what's built vs. what's designed-for-later

### Phase 7 — Integration Buffer (Weeks 29–30, new)
- No new features. Reserved explicitly for: fixing whatever breaks when all components run together under load, a last look at the cascade-simulation edge cases, rehearsing the live demo end-to-end at least twice, and addressing anything Grafana's load test surfaces in Phase 5.

**Deliverable:** A rehearsed, working system — not a system that "should work."

---

## 12. WHAT MAKES THIS PROJECT HARD TO FAKE OR SIMPLIFY

Unchanged from v1.0, still true at 5 agents:

1. **LangGraph agent state machine** — agents must properly coordinate state and pass structured, validated data between nodes, or the system produces garbage.
2. **TimescaleDB time-bucket queries** — correct hypertable queries for rolling averages, anomaly windows, and compression policies require real database engineering skill.
3. **OR-Tools CVRP** — correct distance matrix construction, skill-matching, and time-window constraints; it will not work if implemented carelessly.
4. **Row-Level Security** — policies must be tested against explicit cross-tenant attack scenarios; a misconfigured policy leaks data, which is both a technical and a legal problem.
5. **NetworkX cascade simulation** — modeling dependency graphs and propagating failure states through cycles without infinite loops requires correct graph algorithm application.
6. **MapLibre private tile system with sandboxed ingestion** — wiring sandboxed-upload → GDAL → MinIO → tile server → MapLibre with a non-standard coordinate system, safely, is not a single-tutorial problem.

---

## 13. RISKS AND MITIGATION (Updated)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Agent pipeline too slow for real-time use | Medium | High | Debounced triggers (§7.1); async via Celery; incremental result display |
| LLM API costs exceed budget | Medium | Medium | Debounced triggers; cache agent outputs; only re-run when data changes |
| Agent hallucinates invalid output (bad asset ID, nonsensical schedule) | Medium | High | Pydantic validation on every agent output + retry-then-escalate path (§4.4) |
| MapLibre + private tiles coordinate mismatch | Medium | High | Dedicate 2 weeks; validate with 3 different floor plan formats |
| Malicious or malformed uploaded floor plan | Medium | High | Sandboxed processing worker, size limits, active-content stripping, fuzz testing in Phase 5 |
| RLS misconfiguration leaks tenant data | Low | Critical | Automated cross-tenant tests on every CI build |
| OR-Tools solver timeout for large facilities | Medium | Medium | 5-second solver time limit; return best-found solution |
| Self-managing HA databases eats schedule | High (in v1.0) | High | **Eliminated in v2.0** — managed services used for build/demo instead |
| Full 12-service K8s deployment eats schedule | High (in v1.0) | High | **Reduced in v2.0** — partial real k3s deployment + documented full design |
| Scope creep into IoT hardware integration | High | High | Explicitly scoped as simulated; real integration documented as future work |
| No buffer for integration issues at the end | High (in v1.0) | High | **Added** — Phase 7 is a dedicated 2-week integration/rehearsal buffer |

---

## 14. WHAT THE EVALUATING FACULTY SHOULD EXPECT TO SEE

- [ ] A real facility map loaded from an uploaded floor plan file, processed through the sandboxed pipeline, rendered in the browser
- [ ] Assets placed on the map, color-coded by live risk score
- [ ] A sensor anomaly fired that triggers a real-time alert to a connected browser in under 2 seconds
- [ ] The full 5-agent pipeline running end-to-end and producing a maintenance schedule, with output validation visibly logged
- [ ] "What if Pump 7 shuts down?" simulation producing an impact report with affected assets highlighted on the map
- [ ] Two browser windows logged in as different tenants — confirming they cannot see each other's data
- [ ] Grafana showing system metrics during a load test with 20+ concurrent users
- [ ] The CI/CD pipeline running and deploying at least one service to a real k3s cluster, with autoscaling demonstrated
- [ ] A short, explicit walkthrough of what's built vs. what's documented as future production work (Appendix A) — this is a strength, not a weakness, when presented honestly

---

## 15. TEAM SIZE RECOMMENDATION (Updated)

| Scenario | Size | Reality |
|---|---|---|
| Solo student | 1 person | Feasible at 5 agents with managed data services and partial k3s. Cut OAuth/SSO (already deferred above). Still a genuinely impressive project if the 5 agents, RLS isolation, and simulation work correctly. |
| 2-person team | 2 people | Comfortable in 6–7 months. Split: 1 on backend/agents/data, 1 on frontend/map/infra. This is the sweet spot for the v2.0 scope. |
| 3-person team | 3 people | Room to reinstate a couple of v1.0 features if desired — OAuth/SSO, full self-hosted HA data layer, or splitting Agent 5 back into two separate LangGraph nodes for cleaner demoability. |

---

## 16. REFERENCES

- LangGraph documentation (state machine model, checkpointing) — verify current version and capabilities at time of build, since this space moves fast
- TimescaleDB documentation (hypertables, compression, continuous aggregates)
- Google OR-Tools documentation (CVRP solver)
- OWASP guidance on file upload handling and untrusted input sandboxing
- PostgreSQL Row-Level Security documentation

*Market-sizing and framework-adoption statistics from v1.0 have been removed. If you want to reinstate them for the write-up, verify each figure against its primary source (report name, publisher, page/section) before citing — don't cite a secondary aggregator's summary of a number.*

---

## APPENDIX A — Full Production Design (Documented, Not Built)

For completeness and to show you understand the gap between "capstone demo" and "production system," this appendix should describe (in your final submission):

- Self-hosted HA PostgreSQL and TimescaleDB with primary + read replica, connection pooling (PgBouncer), and failover strategy
- Full 12+ service Kubernetes namespace with HPA tuned from real load-test data, not defaults
- Per-tenant MinIO bucket isolation with lifecycle policies
- OAuth2/SSO (Google/Microsoft) with full enterprise identity federation
- Multi-region deployment considerations for facilities in different jurisdictions
- Real IoT integration (OPC-UA, MQTT broker clustering) replacing the simulated data generator

This section exists so the committee sees the full production vision without you having to build and de-risk all of it under a capstone deadline.
