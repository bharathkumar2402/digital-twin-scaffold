# CLAUDE.md — infra/

Layered on top of the root CLAUDE.md. Applies to everything under `infra/`.

## Conventions specific to this folder

- `infra/docker/` is the primary, always-working environment. Anything added here must
  keep `docker compose up` working end to end for local dev and the live demo.
- `infra/k3s/` holds real, deployable manifests — but ONLY for the partial demo deployment:
  API service, one Celery worker, frontend. Do not add manifests here for the full
  12-service namespace or for stateful data services (Postgres/Timescale/Redis) — those
  are managed services, connected via env vars/secrets, not deployed by us.
- `infra/appendix-a-future-prod/` is documentation only (markdown, diagrams, example
  manifests marked clearly as non-functional reference). Nothing in this folder should be
  wired into CI or expected to actually run. If asked to "productionize" something, put
  the design here first and confirm before writing runnable code.
- Any new GitHub Actions workflow should run: lint → unit tests → integration tests
  (testcontainers) → build → (on main) deploy. Don't add a deploy step for anything beyond
  the k3s partial deployment without discussion.
