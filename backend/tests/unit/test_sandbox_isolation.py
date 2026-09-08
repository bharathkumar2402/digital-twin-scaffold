"""Isolation-boundary tests for the upload sandbox (issue 2.1).

This is the "extra scrutiny" test set CLAUDE.md calls out for Phase 2 task 1: the task
is explicitly to *build and prove* the isolation boundary, not just wire an endpoint.
These checks are structural (static imports, compose config), not behavioral — the goal
is to make the boundary hold even if someone later adds a "quick" DB call to the sandbox
package without thinking about it.
"""

import ast
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SANDBOX_DIR = BACKEND_DIR / "app" / "sandbox"
COMPOSE_FILE = BACKEND_DIR.parent / "infra" / "docker" / "docker-compose.yml"

# Anything that would give the sandbox worker a path to Postgres/Timescale, or to the
# rest of the main app's code (which itself imports app.core.db).
FORBIDDEN_IMPORT_ROOTS = {
    "sqlalchemy",
    "asyncpg",
    "psycopg2",
    "app.core.db",
    "app.core.tenant_context",
    "app.models",
    "app.services",
    "app.api",
    "app.workers",
    "app.main",
}


def _imported_module_roots(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


def test_sandbox_package_exists_and_has_files() -> None:
    py_files = sorted(SANDBOX_DIR.glob("*.py"))
    assert py_files, "app/sandbox/ should contain the sandbox worker's code"


def test_sandbox_package_never_imports_db_or_main_app_code() -> None:
    """Static guarantee, not a runtime one: even a sandbox task that is never invoked
    in this test run can't quietly import SQLAlchemy or app.models/services/api. Catches
    a future "just import the model to save a round trip" shortcut at review time."""
    offenders: dict[str, set[str]] = {}
    for py_file in SANDBOX_DIR.glob("*.py"):
        imported = _imported_module_roots(py_file)
        hits = {
            imp
            for imp in imported
            if any(
                imp == forbidden or imp.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_IMPORT_ROOTS
            )
        }
        if hits:
            offenders[py_file.name] = hits

    assert offenders == {}, f"sandbox package imports forbidden modules: {offenders}"


def test_sandbox_dockerfile_copies_only_sandbox_code() -> None:
    dockerfile = (BACKEND_DIR / "Dockerfile.sandbox").read_text(encoding="utf-8")
    copy_lines = [line for line in dockerfile.splitlines() if line.strip().startswith("COPY")]
    assert copy_lines, "Dockerfile.sandbox should COPY its source explicitly"
    for line in copy_lines:
        assert "app/sandbox" in line or "app/__init__.py" in line, (
            f"Dockerfile.sandbox COPYs something beyond app/__init__.py and app/sandbox: {line!r}"
        )
    # The rest of the app (app/core, app/models, app/services, app/api) must not be
    # reachable in this image at all.
    for forbidden_path in ("app/core", "app/models", "app/services", "app/api"):
        assert forbidden_path not in dockerfile


def _compose_service_block(compose_text: str, service_name: str) -> str:
    """Extracts a top-level service's YAML block by indentation, without adding a
    YAML-parsing dependency just for this text-shaped check."""
    lines = compose_text.splitlines()
    pattern = rf"^  {re.escape(service_name)}:\s*$"
    start = next(i for i, line in enumerate(lines) if re.match(pattern, line))
    end = start + 1
    while end < len(lines) and (lines[end].startswith("    ") or lines[end].strip() == ""):
        end += 1
    return "\n".join(lines[start:end])


def test_upload_sandbox_service_has_no_db_credentials_or_shared_volume() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    block = _compose_service_block(compose_text, "upload-sandbox")

    # It must load its own narrow env file, never the shared `.env` that carries
    # POSTGRES_*/TIMESCALE_*/APP_DB_*/JWT_* for every other backend service here.
    assert "env_file: .env.sandbox" in block
    assert "env_file: .env\n" not in block + "\n"

    # No bind-mount of the backend source tree (unlike `api`, which mounts the whole
    # repo for --reload) and no shared uploads/filesystem volume of any kind.
    assert "volumes:" not in block


def test_upload_sandbox_service_uses_its_own_dockerfile() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    block = _compose_service_block(compose_text, "upload-sandbox")
    assert "Dockerfile.sandbox" in block


def test_sandbox_and_main_celery_apps_use_disjoint_default_queues() -> None:
    from app.core.celery_app import celery_app
    from app.sandbox.celery_app import sandbox_celery_app

    assert celery_app.conf.task_default_queue != sandbox_celery_app.conf.task_default_queue
    assert sandbox_celery_app.conf.task_default_queue == "upload-sandbox"
    assert celery_app.conf.task_routes["record_sandbox_result"]["queue"] == "sandbox-results"
    assert celery_app.conf.task_routes["record_sandbox_result"]["queue"] != (
        sandbox_celery_app.conf.task_default_queue
    )
