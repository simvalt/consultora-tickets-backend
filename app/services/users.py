from sqlalchemy import Connection

from app.deps import CurrentUser, load_permissions
from app.errors import bad_request, conflict, forbidden, not_found
from app.permissions import within
from app.repositories import roles as roles_repo
from app.repositories import users as repo
from app.schemas import UserCreate, UserUpdate
from app.security import hash_password


def _get(conn: Connection, user_id: int, lock: bool = False) -> dict:
    user = repo.get(conn, user_id, lock)
    if not user:
        raise not_found("El usuario no existe.")
    return user


def _assignable_role(conn: Connection, actor: CurrentUser, role_id: int) -> None:
    role = roles_repo.get(conn, role_id)
    if not role:
        raise bad_request("El rol indicado no existe.", "invalid_role")
    if not role["is_active"]:
        raise bad_request("El rol indicado está inactivo.", "invalid_role")
    if not within(actor.permissions, role["permissions"]):
        raise forbidden("No puedes asignar un rol con más privilegios que los tuyos.")


def _can_manage(conn: Connection, actor: CurrentUser, target: dict) -> None:
    """Un usuario no puede modificar a quien tenga más privilegios que él."""
    if not within(actor.permissions, load_permissions(conn, target["role_id"])):
        raise forbidden("No puedes modificar a un usuario con más privilegios que los tuyos.")


def _ensure_admin_remains(conn: Connection) -> None:
    if repo.count_active_admins(conn) == 0:
        raise conflict("La operación dejaría al sistema sin un administrador activo.", "last_admin")


def create(conn: Connection, actor: CurrentUser, data: UserCreate) -> dict:
    _assignable_role(conn, actor, data.role_id)
    if repo.email_taken(conn, data.email):
        raise conflict("Ya existe un usuario con ese correo.", "email_taken")
    uid = repo.insert(conn, data.email, data.full_name, hash_password(data.password), data.role_id)
    return _get(conn, uid)


def update(conn: Connection, actor: CurrentUser, user_id: int, data: UserUpdate) -> dict:
    repo.lock_admin_changes(conn)
    target = _get(conn, user_id, lock=True)
    _can_manage(conn, actor, target)
    fields: dict = {}
    if data.full_name is not None:
        fields["full_name"] = data.full_name
    if data.email is not None and data.email != target["email"]:
        if repo.email_taken(conn, data.email, user_id):
            raise conflict("Ya existe un usuario con ese correo.", "email_taken")
        fields["email"] = data.email
    role_changed = data.role_id is not None and data.role_id != target["role_id"]
    if role_changed:
        if user_id == actor.id:
            raise forbidden("No puedes cambiar tu propio rol.")
        _assignable_role(conn, actor, data.role_id)
        fields["role_id"] = data.role_id
    if data.password is not None:
        fields["password_hash"] = hash_password(data.password)
    if fields:
        repo.update(conn, user_id, fields)
    if role_changed or data.password is not None:
        repo.revoke_tokens(conn, user_id)  # fuerza reautenticación tras cambiar rol o contraseña
    if role_changed:
        _ensure_admin_remains(conn)
    return _get(conn, user_id)


def deactivate(conn: Connection, actor: CurrentUser, user_id: int) -> dict:
    repo.lock_admin_changes(conn)
    target = _get(conn, user_id, lock=True)
    if user_id == actor.id:
        raise conflict("No puedes desactivar tu propio usuario.", "self_deactivation")
    _can_manage(conn, actor, target)
    repo.update(conn, user_id, {"is_active": False})
    repo.revoke_tokens(conn, user_id)
    _ensure_admin_remains(conn)
    return _get(conn, user_id)


def activate(conn: Connection, actor: CurrentUser, user_id: int) -> dict:
    target = _get(conn, user_id, lock=True)
    _can_manage(conn, actor, target)
    repo.update(conn, user_id, {"is_active": True})
    return _get(conn, user_id)
