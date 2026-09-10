from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Env-driven settings for the main Postgres DB (tenants/users/facilities/...)
    and the separate managed TimescaleDB instance (sensor_readings only).

    These are two physically separate databases — sensor_readings can't hold a foreign
    key to tenants because Postgres has no cross-database foreign keys — so each gets
    its own connection settings, its own non-superuser runtime role, and its own Alembic
    migration chain (`migrations/` vs `migrations_timescale/`).
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "digital_twin"
    postgres_user: str = "postgres"
    postgres_password: str = ""

    # Non-superuser role the app itself connects as at runtime (NOBYPASSRLS — see
    # migration 0002). `postgres_user` above stays reserved for migrations, which need
    # DDL and role-creation privileges that this role deliberately does not have.
    app_db_user: str = "app_role"
    app_db_password: str = "dev-only-app-role-password-change-me"

    # Separate TimescaleDB instance for sensor_readings. Same admin/app-role split as
    # above, but against a different physical database — its own migration chain
    # (`migrations_timescale/`) creates its own `app_role` (roles are per-cluster, not
    # shared across managed Postgres instances).
    timescale_host: str = "localhost"
    timescale_port: int = 5432
    timescale_db: str = "digital_twin_telemetry"
    timescale_user: str = "postgres"
    timescale_password: str = ""

    app_timescale_user: str = "app_role"
    app_timescale_password: str = "dev-only-app-role-password-change-me"

    # Redis is the Celery broker for both the main app's task queue and the
    # `upload-sandbox` handoff queue (app/sandbox/**) — see PROJECT_PLAN.md §6.
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    # Raw floor-plan bytes go straight to object storage, never to the API container's
    # local disk — the sandbox worker (network-only access) fetches them from here.
    # This bucket holds only untrusted, unsanitized uploads.
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_raw_uploads_bucket: str = "facility-map-raw-uploads"
    # Populated by app/sandbox/convert.py (issue 2.3) with an anonymous-read policy
    # (app/sandbox/storage.py, issue 2.4) — tiles are fetched straight from MinIO, no
    # dedicated tile-server process. See tile_url_template below.
    minio_tiles_bucket: str = "facility-map-tiles"
    # Browser/host-facing MinIO base URL. Distinct from `minio_endpoint`, which is the
    # in-Docker-network address (`minio:9000`) other containers use to reach it — a
    # tile URL handed to a browser must use the host-mapped address instead
    # (`localhost:9000` in the default compose setup).
    minio_public_endpoint: str = "localhost:9000"
    minio_public_secure: bool = False

    # Holds versioned, trained risk-model artifacts (issue 3.2) - model file, feature
    # names, and metrics per version, plus a `latest.json` pointer inference (3.3) reads.
    minio_models_bucket: str = "ml-models"

    max_map_upload_bytes: int = 25 * 1024 * 1024  # 25 MiB

    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Secure-by-default (HTTPS-only refresh cookie). Set to false only for local
    # dev/docker-compose environments served over plain HTTP.
    cookie_secure: bool = True

    # Browser origin the frontend (task 2.5) is served from - the sole entry in
    # CORSMiddleware's allow_origins (app/main.py). Kept to a single explicit origin
    # rather than "*" because allow_credentials=True is required for the refresh
    # cookie, and browsers refuse to combine a wildcard origin with credentials.
    frontend_origin: str = "http://localhost:5173"

    # Built with sqlalchemy.engine.URL.create rather than an f-string: usernames and
    # passwords from managed providers routinely contain "@", ":", "/" or other
    # URL-reserved characters (e.g. Supavisor's "role.project-ref" usernames, or a
    # generated password containing "@") which silently corrupt a hand-built DSN
    # instead of raising — URL.create percent-encodes each component correctly.
    @property
    def database_url(self) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def app_database_url(self) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=self.app_db_user,
            password=self.app_db_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def timescale_migration_url(self) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=self.timescale_user,
            password=self.timescale_password,
            host=self.timescale_host,
            port=self.timescale_port,
            database=self.timescale_db,
        ).render_as_string(hide_password=False)

    @property
    def app_timescale_url(self) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=self.app_timescale_user,
            password=self.app_timescale_password,
            host=self.timescale_host,
            port=self.timescale_port,
            database=self.timescale_db,
        ).render_as_string(hide_password=False)

    def tile_url_template(self, tile_prefix: str) -> str:
        """A MapLibre-style `{z}/{x}/{y}` URL template for one upload's tile pyramid,
        addressed at the browser-facing MinIO endpoint (not the in-network one other
        containers use). Local pixel XYZ coordinates, not a real-world CRS — see
        app/sandbox/convert.py."""
        scheme = "https" if self.minio_public_secure else "http"
        return (
            f"{scheme}://{self.minio_public_endpoint}/{self.minio_tiles_bucket}/"
            f"{tile_prefix}/{{z}}/{{x}}/{{y}}.png"
        )

    @property
    def redis_url(self) -> str:
        return URL.create(
            "redis",
            password=self.redis_password or None,
            host=self.redis_host,
            port=self.redis_port,
        ).render_as_string(hide_password=False)


settings = Settings()
