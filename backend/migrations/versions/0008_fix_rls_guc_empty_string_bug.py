"""fix RLS policies to guard against the empty-string GUC placeholder bug

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-08

Found live-verifying issue 2.7: `users`/`facilities` (0001), `facility_map_uploads`
(0004), and `assets` (0006) all define their RLS policy as:

    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)

Postgres's `current_setting(name, missing_ok)` returns NULL only if the custom GUC has
*never* been referenced in the current session. Once any transaction on that
session/connection has done `SET LOCAL app.current_tenant_id = '<value>'` (which is how
`scope_session_to_tenant` in app/core/tenant_context.py scopes every request) and that
transaction commits, Postgres's custom-GUC placeholder mechanism leaves the setting at
'' (empty string), not NULL, for the rest of the session - confirmed directly against a
real Postgres instance:

    BEGIN;
    SELECT set_config('app.current_tenant_id', '<uuid>', true);
    COMMIT;
    SELECT current_setting('app.current_tenant_id', true) IS NULL;  -- false
    SELECT current_setting('app.current_tenant_id', true) = '';     -- true

A bare `''::uuid` cast raises `InvalidTextRepresentationError`, a hard 500, instead of
the intended fail-closed "0 rows". Because SQLAlchemy's async connection pool can reuse
the same physical connection across an ORM `session.refresh()` immediately after
`session.commit()` within a single request (refresh's SELECT runs in a fresh
transaction, after the commit that ended the `SET LOCAL` scope), this fires on every
`create_asset` call and now would on `create_dependency` too - see migration 0007,
which was written with the fix already, and asset_dependency_service.py /
asset_service.py's `session.refresh()` calls. Guard with
`NULLIF(current_setting(...), '')` so the empty-string case coerces to NULL before the
cast, restoring the intended fail-closed behavior (0 rows) instead of crashing. No
security-semantics change: an unset or empty-string GUC still denies all rows either
way, this only changes "crash" to "cleanly see nothing".
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUC_EXPR_OLD = "current_setting('app.current_tenant_id', true)::uuid"
_GUC_EXPR_NEW = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"

_POLICIES = {
    "users": "tenant_isolation_users",
    "facilities": "tenant_isolation_facilities",
    "facility_map_uploads": "tenant_isolation_facility_map_uploads",
    "assets": "tenant_isolation_assets",
}


def upgrade() -> None:
    for table, policy in _POLICIES.items():
        op.execute(
            f"""
            ALTER POLICY {policy} ON {table}
            USING (tenant_id = {_GUC_EXPR_NEW})
            WITH CHECK (tenant_id = {_GUC_EXPR_NEW})
            """
        )


def downgrade() -> None:
    for table, policy in _POLICIES.items():
        op.execute(
            f"""
            ALTER POLICY {policy} ON {table}
            USING (tenant_id = {_GUC_EXPR_OLD})
            WITH CHECK (tenant_id = {_GUC_EXPR_OLD})
            """
        )
