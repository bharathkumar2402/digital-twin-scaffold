from celery import Celery

from app.core.config import settings

# The main app's Celery app. It sends jobs onto the dedicated "upload-sandbox" queue
# (consumed only by app/sandbox/celery_app.py, a separate Celery app/container with no
# DB access) and consumes results back on "sandbox-results" via
# app/workers/callback_tasks.py, which is the only place that writes the sandbox's
# result into Postgres. It never runs sandbox tasks itself.
celery_app = Celery("digital_twin", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.task_default_queue = "celery"
celery_app.conf.task_routes = {
    "record_sandbox_result": {"queue": "sandbox-results"},
}

# Import so `record_sandbox_result`/`compute_facility_risk_scores` register on this
# app when the worker starts (`celery -A app.core.celery_app worker`, per
# infra/docker/docker-compose.yml).
from app.workers import callback_tasks, risk_tasks  # noqa: E402,F401
