"""Métricas del dashboard.

Fechas usadas:
- Indicadores, distribuciones y tiempo de resolución: el periodo se aplica a `tickets.created_at`.
- Serie «creados»: `tickets.created_at`. Serie «cerrados»: fecha de cada evento de cierre
  (`ticket_events` con to_status = 'cerrado'); un ticket reabierto y vuelto a cerrar cuenta en cada cierre.
- Los días se agrupan en UTC. Los tickets eliminados lógicamente no se cuentan.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, text

from app import workflow as wf
from app.errors import bad_request
from app.repositories.tickets import day_end, day_start

MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30


@dataclass
class DashboardFilters:
    date_from: date | None = None
    date_to: date | None = None
    client_id: int | None = None
    assignee_id: int | None = None
    priority: str | None = None
    category_id: int | None = None


def _common(f: DashboardFilters) -> tuple[list[str], dict[str, Any]]:
    where, b = ["t.deleted_at IS NULL"], {}
    for col, val in (
        ("client_id", f.client_id), ("assignee_id", f.assignee_id), ("priority", f.priority),
        ("category_id", f.category_id),
    ):
        if val is not None:
            where.append(f"t.{col} = :{col}")
            b[col] = val
    return where, b


def _period(f: DashboardFilters, where: list[str], b: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    where, b = list(where), dict(b)
    if f.date_from:
        where.append("t.created_at >= :p_from")
        b["p_from"] = day_start(f.date_from)
    if f.date_to:
        where.append("t.created_at < :p_to")
        b["p_to"] = day_end(f.date_to)
    return where, b


def series_range(f: DashboardFilters) -> tuple[date, date]:
    end = f.date_to or date.today()
    start = f.date_from or (end - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if start > end:
        raise bad_request("La fecha inicial no puede ser posterior a la final.", "invalid_range")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise bad_request(f"El periodo no puede exceder {MAX_RANGE_DAYS} días.", "invalid_range")
    return start, end


def compute(conn: Connection, f: DashboardFilters) -> dict:
    start, end = series_range(f)
    common, cb = _common(f)
    where, b = _period(f, common, cb)
    w = " AND ".join(where)

    open_list = ", ".join(f"'{s}'" for s in wf.OPEN_STATUSES)
    # Un solo recorrido para estatus, prioridad, vencidos y tiempo de resolución (antes: cuatro consultas).
    # Se agrupa por (estatus, prioridad): como mucho 28 filas, que se reducen en Python.
    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    overdue = 0
    closed_seconds = 0.0
    for status, priority, n, n_overdue, seconds in conn.execute(
        text(
            f"SELECT t.status, t.priority, count(*), "
            f"count(*) FILTER (WHERE t.due_at < now() AND t.status IN ({open_list})), "
            f"coalesce(sum(extract(epoch FROM (t.closed_at - t.created_at))) FILTER (WHERE t.status = 'cerrado'), 0) "
            f"FROM tickets t WHERE {w} GROUP BY t.status, t.priority"
        ),
        b,
    ):
        by_status[status] = by_status.get(status, 0) + n
        by_priority[priority] = by_priority.get(priority, 0) + n
        overdue += n_overdue
        closed_seconds += float(seconds)
    closed_count = by_status.get(wf.CERRADO, 0)
    avg_hours = closed_seconds / closed_count / 3600.0 if closed_count else None
    by_assignee = conn.execute(
        text(
            f"SELECT t.assignee_id AS id, coalesce(u.full_name, 'Sin responsable') AS label, count(*) AS n "
            f"FROM tickets t LEFT JOIN users u ON u.id = t.assignee_id "
            f"WHERE {w} AND t.status IN ({open_list}) GROUP BY t.assignee_id, u.full_name ORDER BY n DESC, label"
        ),
        b,
    ).all()
    by_client = conn.execute(
        text(
            f"SELECT t.client_id AS id, c.name AS label, count(*) AS n FROM tickets t JOIN clients c ON c.id = t.client_id "
            f"WHERE {w} GROUP BY t.client_id, c.name ORDER BY n DESC, label"
        ),
        b,
    ).all()

    # Series por día (UTC) dentro de [start, end].
    sb = {**cb, "s_from": day_start(start), "s_to": day_end(end)}
    cw = " AND ".join(common)
    created = dict(
        conn.execute(
            text(
                f"SELECT (t.created_at AT TIME ZONE 'UTC')::date AS d, count(*) FROM tickets t "
                f"WHERE {cw} AND t.created_at >= :s_from AND t.created_at < :s_to GROUP BY d"
            ),
            sb,
        ).all()
    )
    closed = dict(
        conn.execute(
            text(
                f"SELECT (e.created_at AT TIME ZONE 'UTC')::date AS d, count(*) FROM ticket_events e "
                f"JOIN tickets t ON t.id = e.ticket_id WHERE {cw} AND e.event_type = 'status_change' "
                f"AND e.to_status = 'cerrado' AND e.created_at >= :s_from AND e.created_at < :s_to GROUP BY d"
            ),
            sb,
        ).all()
    )
    series = []
    day = start
    while day <= end:
        series.append({"date": day.isoformat(), "created": created.get(day, 0), "closed": closed.get(day, 0)})
        day += timedelta(days=1)

    return {
        "total": sum(by_status.values()),
        "open": sum(by_status.get(s, 0) for s in wf.OPEN_STATUSES),
        "pending": by_status.get(wf.PENDIENTE, 0),
        "closed": by_status.get(wf.CERRADO, 0),
        "cancelled": by_status.get(wf.CANCELADO, 0),
        "rejected": by_status.get(wf.RECHAZADO, 0),
        "overdue": overdue,
        "avg_resolution_hours": round(float(avg_hours), 2) if avg_hours is not None else None,
        "by_status": [{"key": s, "label": wf.STATUS_LABELS[s], "count": by_status.get(s, 0)} for s in wf.STATUSES],
        "by_priority": [
            {"key": p, "label": wf.PRIORITY_LABELS[p], "count": by_priority.get(p, 0)} for p in wf.PRIORITIES
        ],
        "open_by_assignee": [{"key": str(r.id or 0), "label": r.label, "count": r.n} for r in by_assignee],
        "by_client": [{"key": str(r.id), "label": r.label, "count": r.n} for r in by_client],
        "series": series,
        "series_from": start.isoformat(),
        "series_to": end.isoformat(),
    }
