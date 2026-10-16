from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import Connection

from app.csvutil import csv_response
from app.db import DB
from app.deps import CurrentUser, require
from app.permissions import Level
from app.schemas import DashboardOut, Priority
from app.services import dashboard as service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def dashboard_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    client_id: int | None = None,
    assignee_id: int | None = None,
    priority: Priority | None = None,
    category_id: int | None = None,
) -> service.DashboardFilters:
    return service.DashboardFilters(date_from, date_to, client_id, assignee_id, priority, category_id)


@router.get("", response_model=DashboardOut, summary="Métricas del dashboard")
def get_dashboard(
    f: service.DashboardFilters = Depends(dashboard_filters),
    _: CurrentUser = Depends(require("dashboard", Level.READ)),
    conn: Connection = DB,
):
    return service.compute(conn, f)


@router.get("/export", summary="Exportar a CSV las tablas del dashboard (mismos filtros)")
def export_dashboard(
    f: service.DashboardFilters = Depends(dashboard_filters),
    _: CurrentUser = Depends(require("dashboard", Level.READ)),
    conn: Connection = DB,
):
    d = service.compute(conn, f)
    rows: list[list[object]] = [
        ["Indicador", "Total de tickets", d["total"]],
        ["Indicador", "Abiertos", d["open"]],
        ["Indicador", "Pendientes", d["pending"]],
        ["Indicador", "Cerrados", d["closed"]],
        ["Indicador", "Cancelados", d["cancelled"]],
        ["Indicador", "Rechazados", d["rejected"]],
        ["Indicador", "Vencidos", d["overdue"]],
        ["Indicador", "Tiempo promedio de resolución (horas)", d["avg_resolution_hours"]],
    ]
    for section, key in (
        ("Por estatus", "by_status"), ("Por prioridad", "by_priority"),
        ("Abiertos por responsable", "open_by_assignee"), ("Tickets por cliente", "by_client"),
    ):
        rows += [[section, i["label"], i["count"]] for i in d[key]]
    rows += [["Creados por día", p["date"], p["created"]] for p in d["series"]]
    rows += [["Cerrados por día", p["date"], p["closed"]] for p in d["series"]]
    return csv_response("dashboard", ["Sección", "Elemento", "Valor"], rows)
