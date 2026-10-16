from sqlalchemy import Connection

from app.deps import CurrentUser
from app.errors import conflict, forbidden, not_found
from app.permissions import MODULES, within
from app.repositories import roles as repo
from app.repositories import users as users_repo
from app.schemas import RoleCreate, RoleUpdate


def _get(conn: Connection, role_id: int, lock: bool = False) -> dict:
    role = repo.get(conn, role_id, lock)
    if not role:
        raise not_found("El rol no existe.")
    return role


def _full(perms: dict[str, int]) -> dict[str, int]:
    return {m: perms.get(m, 0) for m in MODULES}


def _check_grant(actor: CurrentUser, perms: dict[str, int]) -> None:
    if not within(actor.permissions, perms):
        raise forbidden("No puedes otorgar permisos superiores a los que tú tienes.")


def _ensure_admin_remains(conn: Connection) -> None:
    if users_repo.count_active_admins(conn) == 0:
        raise conflict("La operación dejaría al sistema sin un administrador activo.", "last_admin")


def create(conn: Connection, actor: CurrentUser, data: RoleCreate) -> dict:
    perms = _full(data.permissions)
    _check_grant(actor, perms)
    if repo.name_taken(conn, data.name):
        raise conflict("Ya existe un rol con ese nombre.", "name_taken")
    rid = repo.insert(conn, data.name, data.description)
    repo.set_permissions(conn, rid, perms)
    return _get(conn, rid)


def _guard_target(actor: CurrentUser, role: dict) -> None:
    if role["id"] == actor.role_id:
        raise forbidden("No puedes modificar el rol que tienes asignado.")
    if not within(actor.permissions, role["permissions"]):
        raise forbidden("No puedes modificar un rol con más privilegios que los tuyos.")


def update(conn: Connection, actor: CurrentUser, role_id: int, data: RoleUpdate) -> dict:
    users_repo.lock_admin_changes(conn)
    role = _get(conn, role_id, lock=True)
    _guard_target(actor, role)
    fields: dict = {}
    if data.name is not None and data.name != role["name"]:
        if repo.name_taken(conn, data.name, role_id):
            raise conflict("Ya existe un rol con ese nombre.", "name_taken")
        fields["name"] = data.name
    if data.description is not None:
        fields["description"] = data.description
    if data.permissions is not None:
        perms = _full({**role["permissions"], **data.permissions})
        _check_grant(actor, perms)
        repo.set_permissions(conn, role_id, perms)
    repo.update(conn, role_id, fields)
    _ensure_admin_remains(conn)
    return _get(conn, role_id)


def deactivate(conn: Connection, actor: CurrentUser, role_id: int) -> dict:
    users_repo.lock_admin_changes(conn)
    role = _get(conn, role_id, lock=True)
    _guard_target(actor, role)
    if repo.active_user_count(conn, role_id) > 0:
        raise conflict("El rol tiene usuarios activos asignados; reasígnalos antes de desactivarlo.", "role_in_use")
    repo.update(conn, role_id, {"is_active": False})
    _ensure_admin_remains(conn)
    return _get(conn, role_id)


def activate(conn: Connection, actor: CurrentUser, role_id: int) -> dict:
    role = _get(conn, role_id, lock=True)
    _guard_target(actor, role)
    repo.update(conn, role_id, {"is_active": True})
    return _get(conn, role_id)
