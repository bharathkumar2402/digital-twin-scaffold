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

# Scheduled scoring (Phase L task L.4): `celery -A app.core.celery_app beat` runs one pass over
# every facility each interval. The task skips facilities with no new readings and locks per
# facility, so a run that outlasts the interval cannot overlap the next one.
celery_app.conf.beat_schedule = {
    "score-all-facilities": {
        "task": "score_all_facilities",
        "schedule": settings.scoring_interval_seconds,
    },
}

# Import so `record_sandbox_result`/`compute_facility_risk_scores`/`score_all_facilities` register
# on this app when the worker starts (`celery -A app.core.celery_app worker`, per
# infra/docker/docker-compose.yml).
from app.workers import (  # noqa: E402,F401
    auto_analysis,
    callback_tasks,
    risk_tasks,
    scheduled_scoring,
)
