# CLAUDE.md — backend/

Layered on top of the root CLAUDE.md. Applies to everything under `backend/`.

## Conventions specific to this folder

- All routes are async (`async def`), all DB access goes through async SQLAlchemy sessions.
- Every route handler pulls `tenant_id` from the JWT via the tenant context dependency —
  never accept `tenant_id` as a raw request parameter for a data-scoped query.
- New models go in `app/models/`, new Pydantic schemas in `app/schemas/` — keep request
  schemas and agent-output-validation schemas in clearly separate files
  (`schemas/requests/` vs `schemas/agent_outputs/`).
- New agent nodes go in `app/agents/`, one file per agent, and must import their output
  schema from `app/schemas/agent_outputs/` — no inline/ad-hoc validation.
- Alembic migrations: one migration per PR, never hand-edit a migration that's already
  been applied to a shared dev database.
- Every new table with `tenant_id`: write the RLS policy in the same PR as the model, and
  add the cross-tenant test in `tests/cross_tenant/` in the same PR — not a follow-up.
