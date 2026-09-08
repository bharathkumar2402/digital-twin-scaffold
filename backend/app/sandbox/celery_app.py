from celery import Celery

from app.sandbox.config import sandbox_settings

# A separate Celery app instance from app.core.celery_app, run by its own container
# (infra/docker/docker-compose.yml's `upload-sandbox` service, backend/Dockerfile.sandbox)
# with its own broker connection. It only ever consumes "upload-sandbox" and only ever
# sends onto "sandbox-results" by task name — it never imports app.core.celery_app, so
# the two processes cannot accidentally share task registration or queue defaults.
sandbox_celery_app = Celery(
    "upload_sandbox", broker=sandbox_settings.redis_url, backend=sandbox_settings.redis_url
)
sandbox_celery_app.conf.task_default_queue = "upload-sandbox"

from app.sandbox import tasks  # noqa: E402,F401
