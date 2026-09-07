from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.telemetry import router as telemetry_router
from app.api.tenants import router as tenants_router
from app.api.users import router as users_router

app = FastAPI(title="Digital Twin API")

app.include_router(auth_router)
app.include_router(tenants_router)
app.include_router(users_router)
app.include_router(telemetry_router)
