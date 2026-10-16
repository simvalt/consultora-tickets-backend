"""Esquemas de entrada/salida. Los de salida nunca incluyen hashes ni tokens internos."""
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.permissions import parse_permissions

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

Priority = Literal["baja", "media", "alta", "critica"]
Status = Literal["sin_inicio", "iniciado", "en_proceso", "pendiente", "cancelado", "cerrado", "rechazado"]

def _reject_nul(value: Any) -> None:
    if isinstance(value, str) and "\x00" in value:
        raise ValueError("El texto contiene caracteres no permitidos.")
    if isinstance(value, dict):
        for k, v in value.items():
            _reject_nul(k)
            _reject_nul(v)
    elif isinstance(value, list):
        for v in value:
            _reject_nul(v)


class Input(BaseModel):
    """Base de todos los cuerpos de entrada: rechaza campos desconocidos y el carácter NUL
    (PostgreSQL no admite NUL en texto; sin esta validación terminaría en un error 500)."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_nul(cls, data: Any) -> Any:
        _reject_nul(data)
        return data


def _as_utc(v: datetime | None) -> datetime | None:
    """Las fechas sin zona horaria se interpretan explícitamente como UTC."""
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# ----------------------------------------------------------------- auth
class LoginIn(Input):
    email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=255)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str
    role_id: int
    role_name: str
    permissions: dict[str, int]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: MeOut


class ChangePasswordIn(Input):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    new_password: Password


# ---------------------------------------------------------------- users
class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role_id: int
    role_name: str
    permissions: dict[str, int] = Field(default_factory=dict)  # efectivos (rol + overrides)
    permission_overrides: dict[str, int] = Field(default_factory=dict)  # sólo los propios del usuario
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class UserCreate(Input):
    email: Email
    full_name: Name
    password: Password
    role_id: int
    permission_overrides: dict[str, int | str] = Field(default_factory=dict)

    @field_validator("permission_overrides")
    @classmethod
    def _perm(cls, v: dict) -> dict[str, int]:
        return _check_permissions(v)


class UserUpdate(Input):
    email: Email | None = None
    full_name: Name | None = None
    password: Password | None = None
    role_id: int | None = None
    # None = sin cambios; {} = quitar todos los overrides; si se envía, REEMPLAZA los overrides actuales.
    permission_overrides: dict[str, int | str] | None = None

    @field_validator("permission_overrides")
    @classmethod
    def _perm(cls, v: dict | None) -> dict[str, int] | None:
        return None if v is None else _check_permissions(v)


# ---------------------------------------------------------------- roles
def _check_permissions(v: dict) -> dict[str, int]:
    """Acepta niveles numéricos o por nombre (leer, crear, editar...) y alias de módulo; devuelve {módulo: nivel}."""
    return parse_permissions(v)


class RoleOut(BaseModel):
    id: int
    name: str
    description: str
    is_active: bool
    permissions: dict[str, int]
    user_count: int = 0
    created_at: datetime
    updated_at: datetime


class RoleCreate(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    permissions: dict[str, int | str] = Field(default_factory=dict)

    @field_validator("permissions")
    @classmethod
    def _perm(cls, v: dict) -> dict[str, int]:
        return _check_permissions(v)


class RoleUpdate(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None
    permissions: dict[str, int | str] | None = None

    @field_validator("permissions")
    @classmethod
    def _perm(cls, v: dict | None) -> dict[str, int] | None:
        return None if v is None else _check_permissions(v)


# -------------------------------------------------------------- clients
class ClientOut(BaseModel):
    id: int
    name: str
    contact_name: str
    email: str
    phone: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ClientCreate(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    contact_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)] = ""
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""


class ClientUpdate(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)] | None = None
    contact_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)] | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None


# -------------------------------------------------------------- tickets
class TicketOut(BaseModel):
    id: int
    folio: str
    title: str
    description: str
    client_id: int
    client_name: str
    category_id: int
    category_name: str
    priority: Priority
    status: Status
    created_by: int
    created_by_name: str
    assignee_id: int | None
    assignee_name: str | None
    resolution: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    closed_at: datetime | None
    due_at: datetime | None
    is_overdue: bool


class TicketDetail(TicketOut):
    allowed_transitions: list[Status]


class TicketCreate(Input):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    description: Text = ""
    client_id: int
    category_id: int
    priority: Priority = "media"
    assignee_id: int | None = None
    due_at: datetime | None = None

    _utc = field_validator("due_at")(_as_utc)


class TicketUpdate(Input):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)] | None = None
    description: Text | None = None
    client_id: int | None = None
    category_id: int | None = None
    priority: Priority | None = None
    due_at: datetime | None = None  # null = quitar el vencimiento

    _utc = field_validator("due_at")(_as_utc)

    @model_validator(mode="after")
    def _not_null(self) -> "TicketUpdate":
        for name in ("title", "description", "client_id", "category_id", "priority"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"El campo «{name}» no puede ser nulo.")
        return self


class AssignIn(Input):
    assignee_id: int | None


class StatusIn(Input):
    status: Status
    reason: Reason | None = None
    resolution: Reason | None = None
    assignee_id: int | None = None


class CommentIn(Input):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class CommentOut(BaseModel):
    id: int
    ticket_id: int
    author_id: int
    author_name: str
    body: str
    created_at: datetime


class EventOut(BaseModel):
    id: int
    ticket_id: int
    event_type: str
    from_status: str | None
    to_status: str | None
    from_assignee_id: int | None
    from_assignee_name: str | None
    to_assignee_id: int | None
    to_assignee_name: str | None
    actor_id: int
    actor_name: str
    reason: str | None
    created_at: datetime


# ------------------------------------------------------------ dashboard
class CountItem(BaseModel):
    key: str
    label: str
    count: int


class SeriesPoint(BaseModel):
    date: str
    created: int
    closed: int


class DashboardOut(BaseModel):
    total: int
    open: int
    pending: int
    closed: int
    cancelled: int
    rejected: int
    overdue: int
    avg_resolution_hours: float | None
    by_status: list[CountItem]
    by_priority: list[CountItem]
    open_by_assignee: list[CountItem]
    by_client: list[CountItem]
    series: list[SeriesPoint]
    series_from: str
    series_to: str


class LookupItem(BaseModel):
    id: int
    name: str


class RoleLookup(LookupItem):
    permissions: dict[str, int]
