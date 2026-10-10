"""Real-facility run config for the 5-agent pipeline.

The agents take their data from `config["configurable"]`; without it each one silently falls
back to its hardcoded sample fixtures (random asset ids, one sample task). This builds the
config the production path must use: the facility's actual assets and dependencies, dedicated
RLS-scoped database sessions, and `require_real_data` so no agent may fall back to a fixture.

Why dedicated sessions rather than the request's: `score_facility` (risk agent) commits, and the
tenant GUC is SET LOCAL, so after that commit the session is unscoped. Sharing one session would
make the final `record_agent_run` insert fail RLS. Each agent session is scoped for the tenant,
used by exactly one agent, and closed when the run ends.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.core.db import async_session_factory, timescale_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.services.asset_dependency_service import list_dependencies
from app.services.asset_service import list_assets


def _asset_dict(asset: Any) -> dict[str, Any]:
    status = asset.status.value if hasattr(asset.status, "value") else str(asset.status)
    return {
        "id": asset.id,
        "name": asset.name,
        "type": asset.type,
        "status": status,
        "x": float(asset.x),
        "y": float(asset.y),
    }


@asynccontextmanager
async def real_pipeline_config(
    *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> AsyncIterator[RunnableConfig]:
    """Run config backed by the facility's real data. Raises 404 for a facility that is not the
    caller tenant's (via `list_assets`), before any agent runs."""
    async with (
        async_session_factory() as load_session,
        async_session_factory() as main_session,
        timescale_session_factory() as timescale_session,
    ):
        await scope_session_to_tenant(load_session, tenant_id)
        assets = await list_assets(load_session, tenant_id=tenant_id, facility_id=facility_id)
        dependencies = await list_dependencies(
            load_session, tenant_id=tenant_id, facility_id=facility_id
        )

        # Scoped only now: a fresh transaction each, used by the risk agent alone.
        await scope_session_to_tenant(main_session, tenant_id)
        await scope_session_to_tenant(timescale_session, tenant_id)

        yield {
            "configurable": {
                "require_real_data": True,
                "assets": [_asset_dict(a) for a in assets],
                "dependencies": [
                    {
                        "parent_asset_id": d.parent_asset_id,
                        "child_asset_id": d.child_asset_id,
                    }
                    for d in dependencies
                ],
                "main_session": main_session,
                "timescale_session": timescale_session,
            }
        }
