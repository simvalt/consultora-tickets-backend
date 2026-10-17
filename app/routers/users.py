from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.csvutil import COLUMNS_QUERY, Column, columns_response, pick_columns
from app.db import DB, get_engine
from app.deps import CurrentUser, require
from app.errors import not_found
from app.permissions import Level
from app.query import PageParams, Sort, fetch_page, page_params, stream_rows
from app.repositories import users as repo
from app.schemas import Page, UserCreate, UserOut, UserUpdate
from app.services import users as service

router = APIRouter(prefix="/users", tags=["Usuarios"])

EXPORT_COLUMNS: dict[str, Column] = {  # nunca incluye hashes, tokens ni permisos internos
    "full_name": ("Nombre", lambda r: r["full_name"]),
    "email": ("Correo", lambda r: r["email"]),
    "role": ("Rol", lambda r: r["role_name"]),
    "is_active": ("Activo", lambda r: r["is_active"]),
    "created_at": ("Creado", lambda r: r["created_at"]),
    "last_login_at": ("Último acceso", lambda r: r["last_login_at"]),
}
SortField = Literal["full_name", "email", "role", "is_active", "created_at", "last_login_at"]


class UserQuery:
    """Parámetros de filtrado/orden compartidos por el listado y la exportación."""

    def __init__(
        self,
        q: str | None = Query(None, max_length=100),
        role_id: int | None = None,
        is_active: bool | None = None,
        sort: SortField = "full_name",
        order: Literal["asc", "desc"] = "asc",
    ):
        self.where, self.binds = repo.filters(q, role_id, is_active)
        self.order = repo.order(Sort(sort, order))


@router.get("", response_model=Page[UserOut], summary="Listar usuarios")
def list_users(
    f: UserQuery = Depends(),
    p: PageParams = Depends(page_params),
    _: CurrentUser = Depends(require("users", Level.READ)),
    conn: Connection = DB,
):
    page = fetch_page(conn, repo.COLS, repo.FROM, f.where, f.binds, f.order, p)
    service.attach(conn, page["items"])
    return page


@router.get("/export", summary="Exportar usuarios a CSV (mismos filtros que el listado)")
def export_users(
    f: UserQuery = Depends(), columns: list[str] = COLUMNS_QUERY,
    _: CurrentUser = Depends(require("users", Level.READ)),
):
    cols = pick_columns(EXPORT_COLUMNS, columns)
    return columns_response("usuarios", cols, stream_rows(get_engine(), repo.COLS, repo.FROM, f.where, f.binds, f.order))


@router.get("/{user_id}", response_model=UserOut, summary="Detalle de usuario")
def get_user(
    user_id: int, _: CurrentUser = Depends(require("users", Level.READ)), conn: Connection = DB
):
    user = repo.get(conn, user_id)
    if not user:
        raise not_found("El usuario no existe.")
    return service.attach(conn, [user])[0]


@router.post("", response_model=UserOut, status_code=201, summary="Crear usuario")
def create_user(
    body: UserCreate, actor: CurrentUser = Depends(require("users", Level.INSERT)), conn: Connection = DB
):
    return service.create(conn, actor, body)


@router.patch("/{user_id}", response_model=UserOut, summary="Editar usuario (nombre, correo, contraseña, rol)")
def update_user(
    user_id: int, body: UserUpdate, actor: CurrentUser = Depends(require("users", Level.UPDATE)),
    conn: Connection = DB,
):
    return service.update(conn, actor, user_id, body)


@router.delete("/{user_id}", response_model=UserOut, summary="Desactivar usuario (no se borra)")
def deactivate_user(
    user_id: int, actor: CurrentUser = Depends(require("users", Level.DELETE)), conn: Connection = DB
):
    return service.deactivate(conn, actor, user_id)


@router.post("/{user_id}/activate", response_model=UserOut, summary="Reactivar usuario")
def activate_user(
    user_id: int, actor: CurrentUser = Depends(require("users", Level.UPDATE)), conn: Connection = DB
):
    return service.activate(conn, actor, user_id)
