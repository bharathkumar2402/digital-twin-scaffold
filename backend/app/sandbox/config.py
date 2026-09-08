from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSettings(BaseSettings):
    """Deliberately its own settings class, not a reuse of `app.core.config.Settings`.

    The sandbox container gets its own env file (infra/docker/.env.sandbox, not the
    shared `.env`) via `env_file:` in docker-compose.yml, containing only Redis/MinIO
    variables — no Postgres/Timescale credentials ever reach this container's
    environment at all, so there's nothing for a DB credential to leak even if this
    class were changed to read one.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_raw_uploads_bucket: str = "facility-map-raw-uploads"
    minio_sanitized_bucket: str = "facility-map-sanitized"

    @property
    def redis_url(self) -> str:
        # No SQLAlchemy in this image on purpose (Dockerfile.sandbox installs only
        # celery[redis] + minio) — built by hand with urllib's quote instead of
        # sqlalchemy.engine.URL, which every other connection string in this project
        # uses (see app/core/config.py).
        auth = f":{quote(self.redis_password, safe='')}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"


sandbox_settings = SandboxSettings()
