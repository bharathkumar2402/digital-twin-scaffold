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


settings = Settings()
