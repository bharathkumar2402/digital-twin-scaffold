from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.facility_maps import router as facility_maps_router
from app.api.telemetry import router as telemetry_router
from app.api.tenants import router as tenants_router
from app.api.users import router as users_router
from app.core.config import settings

app = FastAPI(title="Digital Twin API")

# The frontend (task 2.5) calls this API from its own origin (Vite dev server on
# :5173, this API on :8000) and relies on the HttpOnly refresh cookie set by
# /login and read back by /refresh, so credentials must be allowed - which in turn
# means the origin list can't be "*" (browsers reject wildcard-origin + credentials).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(tenants_router)
app.include_router(users_router)
app.include_router(telemetry_router)
app.include_router(facility_maps_router)
