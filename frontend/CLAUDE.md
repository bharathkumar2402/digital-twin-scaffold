# CLAUDE.md — frontend/

Layered on top of the root CLAUDE.md. Applies to everything under `frontend/`.

## Conventions specific to this folder

- React 18 + TypeScript, function components + hooks only, no class components.
- Server state (API data) goes through React Query — do not hand-roll fetch/useEffect
  data-loading for anything that hits the backend.
- Map rendering goes through MapLibre GL JS — assets are GeoJSON point layers, never
  hand-drawn SVG shapes positioned with CSS.
- Role-based UI gating (RBAC) mirrors the backend roles exactly:
  `superadmin | tenant_admin | facility_manager | technician | viewer`. Check the current
  role from the auth context, not from a locally cached assumption.
- The agent reasoning transparency panel (plan §11 Phase 6) should render
  `validation_errors` from `agent_runs` if present — this is a required field, not optional.
- Never use browser localStorage/sessionStorage for anything auth-related — HttpOnly
  cookie for the refresh token, in-memory only for the access token.
