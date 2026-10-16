from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.csvutil import csv_response
from app.db import get_db, get_engine
from app.deps import CurrentUser, require
from app.errors import not_found
from app.permissions import LEVEL_LABELS, MODULE_LABELS, MODULES, Level
from app.query import PageParams, Sort, fetch_page, page_params, stream_rows
from app.repositories import roles as repo
from app.schemas import Page, RoleCreate, RoleOut, RoleUpdate
from app.services import roles as service

router = APIRouter(prefix="/roles", tags=["Roles y permisos"])
SortField = Literal["name", "is_active", "created_at", "user_count"]


class RoleQuery:
    def __init__(
        self,
        q: str | None = Query(None, max_length=100),
        is_active: bool | None = None,
        sort: SortField = "name",
        order: Literal["asc", "desc"] = "asc",
    ):
        self.where, self.binds = repo.filters(q, is_active)
        self.order = repo.order(Sort(sort, order))


@router.get("", response_model=Page[RoleOut], summary="Listar roles con su matriz de permisos")
def list_roles(
    f: RoleQuery = Depends(),
    p: PageParams = Depends(page_params),
    _: CurrentUser = Depends(require("roles", Level.READ)),
    conn: Connection = Depends(get_db),
):
    page = fetch_page(conn, repo.COLS, repo.FROM, f.where, f.binds, f.order, p)
    repo.attach_permissions(conn, page["items"])
    return page


@router.get("/export", summary="Exportar roles y permisos a CSV (mismos filtros que el listado)")
def export_roles(
    f: RoleQuery = Depends(), _: CurrentUser = Depends(require("roles", Level.READ)),
    conn: Connection = Depends(get_db),
):
    headers = ["Rol", "Descripción", "Activo", "Usuarios", *[MODULE_LABELS[m] for m in MODULES]]
    items = list(stream_rows(get_engine(), repo.COLS, repo.FROM, f.where, f.binds, f.order))
    perms = repo.permissions_for(conn, [r["id"] for r in items])
    rows = (
        [r["name"], r["description"], r["is_active"], r["user_count"],
         *[LEVEL_LABELS[perms[r["id"]][m]] for m in MODULES]]
        for r in items
    )
    return csv_response("roles", headers, rows)


@router.get("/{role_id}", response_model=RoleOut, summary="Detalle de rol")
def get_role(role_id: int, _: CurrentUser = Depends(require("roles", Level.READ)), conn: Connection = Depends(get_db)):
    role = repo.get(conn, role_id)
    if not role:
        raise not_found("El rol no existe.")
    return role


@router.post("", response_model=RoleOut, status_code=201, summary="Crear rol")
def create_role(
    body: RoleCreate, actor: CurrentUser = Depends(require("roles", Level.INSERT)), conn: Connection = Depends(get_db)
):
    return service.create(conn, actor, body)


@router.patch("/{role_id}", response_model=RoleOut, summary="Editar rol y matriz de permisos")
def update_role(
    role_id: int, body: RoleUpdate, actor: CurrentUser = Depends(require("roles", Level.UPDATE)),
    conn: Connection = Depends(get_db),
):
    return service.update(conn, actor, role_id, body)


@router.delete("/{role_id}", response_model=RoleOut, summary="Desactivar rol (no se borra)")
def deactivate_role(
    role_id: int, actor: CurrentUser = Depends(require("roles", Level.DELETE)), conn: Connection = Depends(get_db)
):
    return service.deactivate(conn, actor, role_id)


@router.post("/{role_id}/activate", response_model=RoleOut, summary="Reactivar rol")
def activate_role(
    role_id: int, actor: CurrentUser = Depends(require("roles", Level.UPDATE)), conn: Connection = Depends(get_db)
):
    return service.activate(conn, actor, role_id)
