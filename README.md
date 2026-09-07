# AI Industrial Facility Digital Twin

Multi-tenant digital twin platform: private facility maps, real-time telemetry, and a
5-agent LangGraph pipeline for risk scoring, maintenance planning, route optimization,
inventory checks, and "what-if" shutdown simulation.

- **Full plan (what & why):** [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md)
- **Execution runbook (how, week by week):** [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md) —
  start here for what to actually build next
- **Claude Code context:** [`CLAUDE.md`](CLAUDE.md) (root) + folder-level `CLAUDE.md` in
  `backend/`, `frontend/`, `infra/`

## Getting started

```bash
# 1. Copy env template and fill in managed DB / Redis / Claude API credentials
cp infra/docker/.env.example infra/docker/.env

# 2. Bring up the full dev stack
cd infra/docker
docker compose up --build

# 3. Backend API:      http://localhost:8000/docs
#    Frontend:         http://localhost:5173
#    Grafana:          http://localhost:3000
```

## Team workflow

- One shared repo, feature branches, PRs required into `main`.
- Suggested split: Backend/Agents/Data, Frontend/Map/Infra, (if 3rd person) ML/Testing/Security.
- Every new `tenant_id` table needs an RLS policy + cross-tenant test in the same PR.
- Every agent output needs a Pydantic schema in `backend/app/schemas/agent_outputs/`.
- See `CLAUDE.md` for the full list of non-negotiable engineering rules before you start
  a Claude Code session.

## Current status

See `CLAUDE.md` → "Current phase" — update it as the team progresses through
`docs/PHASE_PLAN.md`. That file has a Phase 0 setup checklist, then Phases 1–7 each
broken into ordered Claude Code sessions with an explicit Definition of Done.
