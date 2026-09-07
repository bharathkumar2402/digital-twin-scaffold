from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven settings for the main Postgres DB (tenants/users/facilities/...).

    TimescaleDB (sensor_readings) is a separate managed instance, configured
    independently once the ingest endpoint session (Phase 1 session 6) needs it.
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

    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Secure-by-default (HTTPS-only refresh cookie). Set to false only for local
    # dev/docker-compose environments served over plain HTTP.
    cookie_secure: bool = True

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def app_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.app_db_user}:{self.app_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
