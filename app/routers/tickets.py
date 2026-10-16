from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.csvutil import csv_response
from app.db import get_db, get_engine
from app.deps import CurrentUser, require
from app.permissions import Level
from app.query import PageParams, Sort, fetch_page, page_params, stream_rows
from app.repositories import tickets as repo
from app.schemas import (
    AssignIn,
    CommentIn,
    CommentOut,
    EventOut,
    Page,
    Priority,
    Status,
    StatusIn,
    TicketCreate,
    TicketDetail,
    TicketOut,
    TicketUpdate,
)
from app.services import tickets as service
from app.workflow import PRIORITY_LABELS, STATUS_LABELS

router = APIRouter(prefix="/tickets", tags=["Tickets"])
SortField = Literal[
    "folio", "title", "client", "category", "priority", "status", "assignee", "created_at", "updated_at", "due_at"
]


class TicketQuery:
    """Filtros y orden compartidos por el listado y la exportación CSV (misma lógica, mismos resultados)."""

    def __init__(
        self,
        q: str | None = Query(None, max_length=100, description="Busca en folio, título y descripción"),
        status: list[Status] = Query(default=[]),
        priority: list[Priority] = Query(default=[]),
        client_id: int | None = None,
        category_id: int | None = None,
        assignee_id: int | None = None,
        unassigned: bool = False,
        created_by: int | None = None,
        overdue: bool = False,
        created_from: date | None = None,
        created_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        sort: SortField = "created_at",
        order: Literal["asc", "desc"] = "desc",
    ):
        self.where, self.binds = repo.where_clause(
            repo.TicketFilters(
                q, list(status), list(priority), client_id, category_id, assignee_id, unassigned, created_by,
                overdue, created_from, created_to, due_from, due_to,
            )
        )
        self.order = repo.order(Sort(sort, order))


@router.get("", response_model=Page[TicketOut], summary="Listar tickets (filtros, orden y paginación en servidor)")
def list_tickets(
    f: TicketQuery = Depends(),
    p: PageParams = Depends(page_params),
    _: CurrentUser = Depends(require("tickets", Level.READ)),
    conn: Connection = Depends(get_db),
):
    return fetch_page(conn, repo.COLS, repo.FROM, f.where, f.binds, f.order, p)


@router.get("/export", summary="Exportar a CSV todos los tickets que coinciden con los filtros")
def export_tickets(f: TicketQuery = Depends(), _: CurrentUser = Depends(require("tickets", Level.READ))):
    headers = [
        "Folio", "Título", "Descripción", "Cliente", "Categoría", "Prioridad", "Estatus", "Creado por",
        "Responsable", "Creado", "Modificado", "Inicio de atención", "Cierre", "Vencimiento", "Resolución",
    ]
    rows = (
        [r["folio"], r["title"], r["description"], r["client_name"], r["category_name"],
         PRIORITY_LABELS[r["priority"]], STATUS_LABELS[r["status"]], r["created_by_name"], r["assignee_name"],
         r["created_at"], r["updated_at"], r["started_at"], r["closed_at"], r["due_at"], r["resolution"]]
        for r in stream_rows(get_engine(), repo.COLS, repo.FROM, f.where, f.binds, f.order)
    )
    return csv_response("tickets", headers, rows)


@router.post("", response_model=TicketDetail, status_code=201, summary="Crear ticket")
def create_ticket(
    body: TicketCreate, actor: CurrentUser = Depends(require("tickets", Level.INSERT)),
    conn: Connection = Depends(get_db),
):
    return service.create(conn, actor, body)


@router.get("/{ticket_id}", response_model=TicketDetail, summary="Detalle de ticket")
def get_ticket(
    ticket_id: int, _: CurrentUser = Depends(require("tickets", Level.READ)), conn: Connection = Depends(get_db)
):
    return service.detail(conn, ticket_id)


@router.patch("/{ticket_id}", response_model=TicketDetail, summary="Editar datos del ticket")
def update_ticket(
    ticket_id: int, body: TicketUpdate, _: CurrentUser = Depends(require("tickets", Level.UPDATE)),
    conn: Connection = Depends(get_db),
):
    return service.update(conn, ticket_id, body)


@router.delete("/{ticket_id}", status_code=204, summary="Eliminación lógica (conserva historial)")
def delete_ticket(
    ticket_id: int, actor: CurrentUser = Depends(require("tickets", Level.DELETE)), conn: Connection = Depends(get_db)
):
    service.delete(conn, actor, ticket_id)


@router.post("/{ticket_id}/assign", response_model=TicketDetail, summary="Asignar o cambiar responsable")
def assign_ticket(
    ticket_id: int, body: AssignIn, actor: CurrentUser = Depends(require("tickets", Level.UPDATE)),
    conn: Connection = Depends(get_db),
):
    return service.assign(conn, actor, ticket_id, body)


@router.post("/{ticket_id}/status", response_model=TicketDetail, summary="Cambiar estatus (transiciones válidas)")
def change_status(
    ticket_id: int, body: StatusIn, actor: CurrentUser = Depends(require("tickets", Level.UPDATE)),
    conn: Connection = Depends(get_db),
):
    return service.change_status(conn, actor, ticket_id, body)


@router.get("/{ticket_id}/comments", response_model=list[CommentOut], summary="Comentarios internos")
def list_comments(
    ticket_id: int, _: CurrentUser = Depends(require("tickets", Level.READ)), conn: Connection = Depends(get_db)
):
    return service.comments(conn, ticket_id)


@router.post("/{ticket_id}/comments", response_model=CommentOut, status_code=201, summary="Agregar comentario")
def add_comment(
    ticket_id: int, body: CommentIn, actor: CurrentUser = Depends(require("tickets", Level.UPDATE)),
    conn: Connection = Depends(get_db),
):
    return service.add_comment(conn, actor, ticket_id, body)


@router.get("/{ticket_id}/events", response_model=list[EventOut], summary="Historial de estatus y asignaciones")
def list_events(
    ticket_id: int, _: CurrentUser = Depends(require("tickets", Level.READ)), conn: Connection = Depends(get_db)
):
    return service.history(conn, ticket_id)
