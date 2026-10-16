from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Connection, text

from app.query import Sort, like, order_sql
from app.workflow import OPEN_STATUSES

_OPEN = ", ".join(f"'{s}'" for s in OPEN_STATUSES)  # constantes del código, no entrada del usuario
COLS = f"""
    t.id, t.folio, t.title, t.description, t.client_id, c.name AS client_name,
    t.category_id, cat.name AS category_name, t.priority, t.status,
    t.created_by, cu.full_name AS created_by_name, t.assignee_id, au.full_name AS assignee_name,
    t.resolution, t.created_at, t.updated_at, t.started_at, t.closed_at, t.due_at,
    (t.due_at IS NOT NULL AND t.due_at < now() AND t.status IN ({_OPEN})) AS is_overdue
"""
FROM = """
    FROM tickets t
    JOIN clients c ON c.id = t.client_id
    JOIN ticket_categories cat ON cat.id = t.category_id
    JOIN users cu ON cu.id = t.created_by
    LEFT JOIN users au ON au.id = t.assignee_id
"""
# Los filtros (where_clause) sólo usan columnas de `t` y los JOIN son 1:1 por FK NOT NULL (o LEFT): el total
# puede contarse sobre la tabla base.
COUNT_FROM = "FROM tickets t"
SORT_COLUMNS = {
    "folio": "t.id", "title": "lower(t.title)", "client": "lower(c.name)", "category": "cat.name",
    "priority": "CASE t.priority WHEN 'baja' THEN 1 WHEN 'media' THEN 2 WHEN 'alta' THEN 3 ELSE 4 END",
    "status": "t.status", "assignee": "lower(au.full_name)", "created_at": "t.created_at",
    "updated_at": "t.updated_at", "due_at": "t.due_at",
}


@dataclass
class TicketFilters:
    q: str | None = None
    status: list[str] = field(default_factory=list)
    priority: list[str] = field(default_factory=list)
    client_id: int | None = None
    category_id: int | None = None
    assignee_id: int | None = None
    unassigned: bool = False
    created_by: int | None = None
    overdue: bool = False
    created_from: date | None = None
    created_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None


def day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def day_end(d: date) -> datetime:
    """Límite superior exclusivo del día `d`."""
    return day_start(d) + timedelta(days=1)


def where_clause(f: TicketFilters) -> tuple[str, dict[str, Any]]:
    where, b = ["t.deleted_at IS NULL"], {}
    if f.q:
        where.append("(t.folio ILIKE :q OR t.title ILIKE :q OR t.description ILIKE :q)")
        b["q"] = like(f.q)
    if f.status:
        where.append("t.status = ANY(:status)")
        b["status"] = list(f.status)
    if f.priority:
        where.append("t.priority = ANY(:priority)")
        b["priority"] = list(f.priority)
    for col, val in (("client_id", f.client_id), ("category_id", f.category_id), ("created_by", f.created_by)):
        if val is not None:
            where.append(f"t.{col} = :{col}")
            b[col] = val
    if f.unassigned:
        where.append("t.assignee_id IS NULL")
    elif f.assignee_id is not None:
        where.append("t.assignee_id = :assignee_id")
        b["assignee_id"] = f.assignee_id
    if f.overdue:
        where.append(f"t.due_at IS NOT NULL AND t.due_at < now() AND t.status IN ({_OPEN})")
    if f.created_from:
        where.append("t.created_at >= :created_from")
        b["created_from"] = day_start(f.created_from)
    if f.created_to:
        where.append("t.created_at < :created_to")
        b["created_to"] = day_end(f.created_to)
    if f.due_from:
        where.append("t.due_at >= :due_from")
        b["due_from"] = day_start(f.due_from)
    if f.due_to:
        where.append("t.due_at < :due_to")
        b["due_to"] = day_end(f.due_to)
    return " AND ".join(where), b


def order(sort: Sort) -> str:
    return order_sql(sort, SORT_COLUMNS, "t.id")


def get(conn: Connection, ticket_id: int) -> dict | None:
    row = conn.execute(
        text(f"SELECT {COLS} {FROM} WHERE t.id = :id AND t.deleted_at IS NULL"), {"id": ticket_id}
    ).mappings().first()
    return dict(row) if row else None


def lock(conn: Connection, ticket_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT id, status, assignee_id, started_at FROM tickets WHERE id = :id AND deleted_at IS NULL FOR UPDATE"),
        {"id": ticket_id},
    ).mappings().first()
    return dict(row) if row else None


def insert(conn: Connection, data: dict[str, Any]) -> int:
    return conn.execute(
        text(
            "INSERT INTO tickets (title, description, client_id, category_id, priority, created_by, assignee_id, "
            "due_at, created_at, updated_at) VALUES (:title, :description, :client_id, :category_id, :priority, "
            ":created_by, :assignee_id, :due_at, :now, :now) RETURNING id"
        ),
        data,
    ).scalar_one()


def update(conn: Connection, ticket_id: int, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)  # claves controladas por el servicio
    conn.execute(text(f"UPDATE tickets SET {sets} WHERE id = :id"), {**fields, "id": ticket_id})


def add_event(
    conn: Connection, ticket_id: int, event_type: str, actor_id: int, now: datetime, *,
    from_status: str | None = None, to_status: str | None = None,
    from_assignee: int | None = None, to_assignee: int | None = None, reason: str | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket_events (ticket_id, event_type, from_status, to_status, from_assignee_id, "
            "to_assignee_id, actor_id, reason, created_at) VALUES (:t, :et, :fs, :ts, :fa, :ta, :a, :r, :now)"
        ),
        {"t": ticket_id, "et": event_type, "fs": from_status, "ts": to_status, "fa": from_assignee,
         "ta": to_assignee, "a": actor_id, "r": reason, "now": now},
    )


def events(conn: Connection, ticket_id: int) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT e.id, e.ticket_id, e.event_type, e.from_status, e.to_status, e.from_assignee_id, "
            "fa.full_name AS from_assignee_name, e.to_assignee_id, ta.full_name AS to_assignee_name, "
            "e.actor_id, a.full_name AS actor_name, e.reason, e.created_at "
            "FROM ticket_events e JOIN users a ON a.id = e.actor_id "
            "LEFT JOIN users fa ON fa.id = e.from_assignee_id LEFT JOIN users ta ON ta.id = e.to_assignee_id "
            "WHERE e.ticket_id = :t ORDER BY e.created_at, e.id"
        ),
        {"t": ticket_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def comments(conn: Connection, ticket_id: int) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT c.id, c.ticket_id, c.author_id, u.full_name AS author_name, c.body, c.created_at "
            "FROM ticket_comments c JOIN users u ON u.id = c.author_id "
            "WHERE c.ticket_id = :t ORDER BY c.created_at, c.id"
        ),
        {"t": ticket_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def add_comment(conn: Connection, ticket_id: int, author_id: int, body: str, now: datetime) -> int:
    return conn.execute(
        text(
            "INSERT INTO ticket_comments (ticket_id, author_id, body, created_at) "
            "VALUES (:t, :a, :b, :now) RETURNING id"
        ),
        {"t": ticket_id, "a": author_id, "b": body, "now": now},
    ).scalar_one()


def comment(conn: Connection, comment_id: int) -> dict:
    row = conn.execute(
        text(
            "SELECT c.id, c.ticket_id, c.author_id, u.full_name AS author_name, c.body, c.created_at "
            "FROM ticket_comments c JOIN users u ON u.id = c.author_id WHERE c.id = :id"
        ),
        {"id": comment_id},
    ).mappings().one()
    return dict(row)
