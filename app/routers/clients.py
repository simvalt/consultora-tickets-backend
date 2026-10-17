from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.csvutil import COLUMNS_QUERY, Column, columns_response, pick_columns
from app.db import DB, get_engine
from app.deps import CurrentUser, require
from app.errors import not_found
from app.permissions import Level
from app.query import PageParams, Sort, fetch_page, page_params, stream_rows
from app.repositories import clients as repo
from app.schemas import ClientCreate, ClientOut, ClientUpdate, Page
from app.services import clients as service

router = APIRouter(prefix="/clients", tags=["Clientes"])

EXPORT_COLUMNS: dict[str, Column] = {
    "name": ("Nombre", lambda r: r["name"]),
    "contact_name": ("Contacto", lambda r: r["contact_name"]),
    "email": ("Correo", lambda r: r["email"]),
    "phone": ("Teléfono", lambda r: r["phone"]),
    "is_active": ("Activo", lambda r: r["is_active"]),
    "created_at": ("Creado", lambda r: r["created_at"]),
}
SortField = Literal["name", "contact_name", "email", "is_active", "created_at"]


class ClientQuery:
    def __init__(
        self,
        q: str | None = Query(None, max_length=100),
        is_active: bool | None = None,
        sort: SortField = "name",
        order: Literal["asc", "desc"] = "asc",
    ):
        self.where, self.binds = repo.filters(q, is_active)
        self.order = repo.order(Sort(sort, order))


@router.get("", response_model=Page[ClientOut], summary="Listar clientes")
def list_clients(
    f: ClientQuery = Depends(),
    p: PageParams = Depends(page_params),
    _: CurrentUser = Depends(require("clients", Level.READ)),
    conn: Connection = DB,
):
    return fetch_page(conn, repo.COLS, repo.FROM, f.where, f.binds, f.order, p)


@router.get("/export", summary="Exportar clientes a CSV (mismos filtros que el listado)")
def export_clients(
    f: ClientQuery = Depends(), columns: list[str] = COLUMNS_QUERY,
    _: CurrentUser = Depends(require("clients", Level.READ)),
):
    cols = pick_columns(EXPORT_COLUMNS, columns)
    return columns_response("clientes", cols, stream_rows(get_engine(), repo.COLS, repo.FROM, f.where, f.binds, f.order))


@router.get("/{client_id}", response_model=ClientOut, summary="Detalle de cliente")
def get_client(
    client_id: int, _: CurrentUser = Depends(require("clients", Level.READ)), conn: Connection = DB
):
    client = repo.get(conn, client_id)
    if not client:
        raise not_found("El cliente no existe.")
    return client


@router.post("", response_model=ClientOut, status_code=201, summary="Crear cliente")
def create_client(
    body: ClientCreate, _: CurrentUser = Depends(require("clients", Level.INSERT)), conn: Connection = DB
):
    return service.create(conn, body)


@router.patch("/{client_id}", response_model=ClientOut, summary="Editar cliente")
def update_client(
    client_id: int, body: ClientUpdate, _: CurrentUser = Depends(require("clients", Level.UPDATE)),
    conn: Connection = DB,
):
    return service.update(conn, client_id, body)


@router.delete("/{client_id}", response_model=ClientOut, summary="Desactivar cliente (conserva sus tickets)")
def deactivate_client(
    client_id: int, _: CurrentUser = Depends(require("clients", Level.DELETE)), conn: Connection = DB
):
    return service.set_active(conn, client_id, False)


@router.post("/{client_id}/activate", response_model=ClientOut, summary="Reactivar cliente")
def activate_client(
    client_id: int, _: CurrentUser = Depends(require("clients", Level.UPDATE)), conn: Connection = DB
):
    return service.set_active(conn, client_id, True)
