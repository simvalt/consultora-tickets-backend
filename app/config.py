from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: str = "local"
    database_url: str
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cookie_samesite: str = "lax"
    cors_origins: str = ""  # lista separada por comas; vacío = sin CORS (mismo origen)
    login_max_attempts: int = 5  # por IP + correo
    login_max_attempts_ip: int = 20  # por IP, sumando todos los correos (credential stuffing)
    login_max_attempts_email: int = 10  # por correo, sumando todas las IPs (ataque distribuido)
    login_window_seconds: int = 300
    docs_enabled: bool | None = None  # None = activados salvo en producción

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        # Fuerza el driver psycopg 3 sea cual sea el esquema recibido.
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        """Impide arrancar en producción con valores inseguros de ejemplo."""
        if self.is_production:
            problems = []
            if "change-me" in self.jwt_secret or "change_me" in self.jwt_secret:
                problems.append("JWT_SECRET es el valor de ejemplo")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE debe ser true (HTTPS)")
            if problems:
                raise ValueError("Configuración insegura para producción: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def show_docs(self) -> bool:
        return (not self.is_production) if self.docs_enabled is None else self.docs_enabled

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
