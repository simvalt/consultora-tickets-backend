from sqlalchemy import Connection

from app.errors import conflict, not_found
from app.repositories import clients as repo
from app.schemas import ClientCreate, ClientUpdate


def _get(conn: Connection, client_id: int) -> dict:
    client = repo.get(conn, client_id)
    if not client:
        raise not_found("El cliente no existe.")
    return client


def create(conn: Connection, data: ClientCreate) -> dict:
    if repo.name_taken(conn, data.name):
        raise conflict("Ya existe un cliente con ese nombre.", "name_taken")
    return _get(conn, repo.insert(conn, data.model_dump()))


def update(conn: Connection, client_id: int, data: ClientUpdate) -> dict:
    current = _get(conn, client_id)
    fields = data.model_dump(exclude_none=True)
    if "name" in fields and fields["name"] != current["name"] and repo.name_taken(conn, fields["name"], client_id):
        raise conflict("Ya existe un cliente con ese nombre.", "name_taken")
    if fields:
        repo.update(conn, client_id, fields)
    return _get(conn, client_id)


def set_active(conn: Connection, client_id: int, active: bool) -> dict:
    _get(conn, client_id)
    repo.update(conn, client_id, {"is_active": active})
    return _get(conn, client_id)
