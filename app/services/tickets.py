from datetime import UTC, datetime

from sqlalchemy import Connection, text

from app import workflow as wf
from app.deps import CurrentUser
from app.errors import bad_request, conflict, forbidden, not_found
from app.permissions import Level
from app.repositories import tickets as repo
from app.schemas import AssignIn, CommentIn, StatusIn, TicketCreate, TicketUpdate


def _now() -> datetime:
    return datetime.now(UTC)


def detail(conn: Connection, ticket_id: int) -> dict:
    ticket = repo.get(conn, ticket_id)
    if not ticket:
        raise not_found("El ticket no existe.")
    ticket["allowed_transitions"] = list(wf.TRANSITIONS[ticket["status"]])
    return ticket


def _locked(conn: Connection, ticket_id: int) -> dict:
    ticket = repo.lock(conn, ticket_id)
    if not ticket:
        raise not_found("El ticket no existe.")
    return ticket


def _check_client(conn: Connection, client_id: int) -> None:
    row = conn.execute(text("SELECT is_active FROM clients WHERE id = :id"), {"id": client_id}).first()
    if row is None:
        raise bad_request("El cliente indicado no existe.", "invalid_client")
    if not row[0]:
        raise bad_request("El cliente indicado está inactivo.", "invalid_client")


def _check_category(conn: Connection, category_id: int) -> None:
    if conn.execute(text("SELECT 1 FROM ticket_categories WHERE id = :id"), {"id": category_id}).first() is None:
        raise bad_request("La categoría indicada no existe.", "invalid_category")


def assignable_users_sql() -> str:
    """Usuarios activos cuyo rol permite trabajar tickets (editar o más)."""
    return (
        "FROM users u JOIN roles r ON r.id = u.role_id AND r.is_active "
        "JOIN role_permissions p ON p.role_id = r.id AND p.module = 'tickets' AND p.level >= 3 "
        "WHERE u.is_active"
    )


def _check_assignee(conn: Connection, assignee_id: int) -> None:
    ok = conn.execute(text(f"SELECT 1 {assignable_users_sql()} AND u.id = :id"), {"id": assignee_id}).first()
    if ok is None:
        raise bad_request("El responsable indicado no existe, está inactivo o no puede atender tickets.", "invalid_assignee")


def create(conn: Connection, actor: CurrentUser, data: TicketCreate) -> dict:
    if data.assignee_id is not None and actor.level("tickets") < Level.UPDATE:
        raise forbidden("Asignar un responsable requiere permiso de edición de tickets.")
    _check_client(conn, data.client_id)
    _check_category(conn, data.category_id)
    if data.assignee_id is not None:
        _check_assignee(conn, data.assignee_id)
    now = _now()
    ticket_id = repo.insert(
        conn, {**data.model_dump(), "created_by": actor.id, "now": now}
    )
    repo.add_event(conn, ticket_id, "created", actor.id, now, to_status=wf.SIN_INICIO)
    if data.assignee_id is not None:
        repo.add_event(conn, ticket_id, "assignment", actor.id, now, to_assignee=data.assignee_id)
    return detail(conn, ticket_id)


def update(conn: Connection, ticket_id: int, data: TicketUpdate) -> dict:
    _locked(conn, ticket_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("title") is None:
        fields.pop("title", None)
    for required in ("client_id", "category_id", "priority"):
        if fields.get(required) is None:
            fields.pop(required, None)
    if fields.get("description") is None:
        fields.pop("description", None)
    if "client_id" in fields:
        current = repo.get(conn, ticket_id)
        if fields["client_id"] != current["client_id"]:
            _check_client(conn, fields["client_id"])
    if "category_id" in fields:
        _check_category(conn, fields["category_id"])
    if fields or "due_at" in data.model_fields_set:
        repo.update(conn, ticket_id, {**fields, "updated_at": _now()})
    return detail(conn, ticket_id)


def assign(conn: Connection, actor: CurrentUser, ticket_id: int, data: AssignIn) -> dict:
    ticket = _locked(conn, ticket_id)
    if ticket["status"] in (wf.CERRADO, wf.CANCELADO, wf.RECHAZADO):
        raise conflict("No se puede cambiar el responsable de un ticket cerrado, cancelado o rechazado.", "ticket_finished")
    if data.assignee_id is None and ticket["status"] != wf.SIN_INICIO:
        raise conflict("Un ticket en atención debe conservar un responsable.", "assignee_required")
    if data.assignee_id is not None:
        _check_assignee(conn, data.assignee_id)
    if data.assignee_id != ticket["assignee_id"]:
        now = _now()
        repo.update(conn, ticket_id, {"assignee_id": data.assignee_id, "updated_at": now})
        repo.add_event(
            conn, ticket_id, "assignment", actor.id, now,
            from_assignee=ticket["assignee_id"], to_assignee=data.assignee_id,
        )
    return detail(conn, ticket_id)


def change_status(conn: Connection, actor: CurrentUser, ticket_id: int, data: StatusIn) -> dict:
    ticket = _locked(conn, ticket_id)
    current, new = ticket["status"], data.status
    if not wf.can_transition(current, new):
        raise conflict(
            f"No se puede pasar de «{wf.STATUS_LABELS[current]}» a «{wf.STATUS_LABELS[new]}».", "invalid_transition"
        )
    reopen = wf.is_reopen(current, new)
    reason = (data.reason or "").strip()
    resolution = (data.resolution or "").strip()
    if (new in wf.REASON_REQUIRED or reopen) and not reason:
        raise bad_request("Debes indicar el motivo del cambio.", "reason_required")
    if new == wf.CERRADO and not resolution:
        raise bad_request("Debes indicar el resumen de la resolución para cerrar el ticket.", "resolution_required")

    now = _now()
    fields: dict = {"status": new, "updated_at": now}
    assignee_id = ticket["assignee_id"]
    if new in wf.ASSIGNEE_REQUIRED:
        wanted = data.assignee_id if data.assignee_id is not None else assignee_id
        if wanted is None:
            raise bad_request("El ticket requiere un responsable para este estatus.", "assignee_required")
        if wanted != assignee_id:
            _check_assignee(conn, wanted)
            fields["assignee_id"] = wanted
            repo.add_event(
                conn, ticket_id, "assignment", actor.id, now, from_assignee=assignee_id, to_assignee=wanted
            )
    if new == wf.INICIADO and ticket["started_at"] is None:
        fields["started_at"] = now
    if new == wf.CERRADO:
        fields["closed_at"] = now
        fields["resolution"] = resolution
    elif reopen:
        fields["closed_at"] = None
        fields["resolution"] = None
    repo.update(conn, ticket_id, fields)
    repo.add_event(
        conn, ticket_id, "reopen" if reopen else "status_change", actor.id, now,
        from_status=current, to_status=new, reason=resolution if new == wf.CERRADO else (reason or None),
    )
    return detail(conn, ticket_id)


def add_comment(conn: Connection, actor: CurrentUser, ticket_id: int, data: CommentIn) -> dict:
    _locked(conn, ticket_id)
    cid = repo.add_comment(conn, ticket_id, actor.id, data.body, _now())
    return repo.comment(conn, cid)


def delete(conn: Connection, actor: CurrentUser, ticket_id: int) -> None:
    _locked(conn, ticket_id)
    now = _now()
    conn.execute(
        text("UPDATE tickets SET deleted_at = :n, deleted_by = :u, updated_at = :n WHERE id = :id"),
        {"n": now, "u": actor.id, "id": ticket_id},
    )
    repo.add_event(conn, ticket_id, "deleted", actor.id, now)


def history(conn: Connection, ticket_id: int) -> list[dict]:
    detail(conn, ticket_id)
    return repo.events(conn, ticket_id)


def comments(conn: Connection, ticket_id: int) -> list[dict]:
    detail(conn, ticket_id)
    return repo.comments(conn, ticket_id)
