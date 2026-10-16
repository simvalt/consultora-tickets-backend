from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cookie_samesite: str = "lax"
    cors_origins: str = ""  # lista separada por comas; vacío = sin CORS (mismo origen)
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    docs_enabled: bool = True

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        # Fuerza el driver psycopg 3 sea cual sea el esquema recibido.
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
