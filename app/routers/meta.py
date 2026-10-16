"""Catálogos y listas ligeras para formularios del frontend."""
from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from app import workflow as wf
from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.errors import forbidden
from app.permissions import LEVEL_LABELS, MODULE_LABELS, MODULES, Level
from app.schemas import LookupItem
from app.services.tickets import assignable_users_sql

router = APIRouter(tags=["Catálogos"])


@router.get("/meta", summary="Módulos, niveles, estatus, prioridades, categorías y transiciones")
def meta(_: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)):
    categories = conn.execute(text("SELECT id, name FROM ticket_categories ORDER BY id")).mappings().all()
    return {
        "modules": [{"key": m, "label": MODULE_LABELS[m]} for m in MODULES],
        "levels": [{"value": k, "label": v} for k, v in LEVEL_LABELS.items()],
        "statuses": [{"value": s, "label": wf.STATUS_LABELS[s]} for s in wf.STATUSES],
        "open_statuses": list(wf.OPEN_STATUSES),
        "priorities": [{"value": p, "label": wf.PRIORITY_LABELS[p]} for p in wf.PRIORITIES],
        "categories": [dict(c) for c in categories],
        "transitions": {s: list(t) for s, t in wf.TRANSITIONS.items()},
        "reason_required": sorted(wf.REASON_REQUIRED),
    }


@router.get("/lookups/clients", response_model=list[LookupItem], summary="Clientes activos (para formularios)")
def lookup_clients(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)):
    if user.level("tickets") < Level.READ and user.level("clients") < Level.READ:
        raise forbidden()
    rows = conn.execute(text("SELECT id, name FROM clients WHERE is_active ORDER BY lower(name)")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/lookups/roles", response_model=list[LookupItem], summary="Roles activos (para formularios)")
def lookup_roles(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)):
    if user.level("users") < Level.READ and user.level("roles") < Level.READ:
        raise forbidden()
    rows = conn.execute(text("SELECT id, name FROM roles WHERE is_active ORDER BY lower(name)")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/lookups/assignees", response_model=list[LookupItem], summary="Usuarios que pueden atender tickets")
def lookup_assignees(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)):
    if user.level("tickets") < Level.READ and user.level("dashboard") < Level.READ:
        raise forbidden()
    rows = conn.execute(
        text(f"SELECT u.id, u.full_name AS name {assignable_users_sql()} ORDER BY lower(u.full_name)")
    ).mappings().all()
    return [dict(r) for r in rows]
