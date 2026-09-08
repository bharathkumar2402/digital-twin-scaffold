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

    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Secure-by-default (HTTPS-only refresh cookie). Set to false only for local
    # dev/docker-compose environments served over plain HTTP.
    cookie_secure: bool = True

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


settings = Settings()
