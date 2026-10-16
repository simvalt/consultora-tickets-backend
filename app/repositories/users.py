from typing import Any

from sqlalchemy import Connection, text

from app.permissions import MODULES
from app.query import Sort, like, order_sql

COLS = (
    "u.id, u.email, u.full_name, u.role_id, r.name AS role_name, u.is_active, "
    "u.created_at, u.updated_at, u.last_login_at"
)
FROM = "FROM users u JOIN roles r ON r.id = u.role_id"
SORT_COLUMNS = {
    "full_name": "lower(u.full_name)", "email": "lower(u.email)", "role": "lower(r.name)",
    "is_active": "u.is_active", "created_at": "u.created_at", "last_login_at": "u.last_login_at",
}


def filters(q: str | None, role_id: int | None, is_active: bool | None) -> tuple[str, dict[str, Any]]:
    where, binds = ["true"], {}
    if q:
        where.append("(u.full_name ILIKE :q OR u.email ILIKE :q)")
        binds["q"] = like(q)
    if role_id is not None:
        where.append("u.role_id = :role_id")
        binds["role_id"] = role_id
    if is_active is not None:
        where.append("u.is_active = :is_active")
        binds["is_active"] = is_active
    return " AND ".join(where), binds


def order(sort: Sort) -> str:
    return order_sql(sort, SORT_COLUMNS, "u.id")


def get(conn: Connection, user_id: int, lock: bool = False) -> dict | None:
    row = conn.execute(
        text(f"SELECT {COLS} {FROM} WHERE u.id = :id" + (" FOR UPDATE OF u" if lock else "")), {"id": user_id}
    ).mappings().first()
    return dict(row) if row else None


def email_taken(conn: Connection, email: str, exclude_id: int | None = None) -> bool:
    return conn.execute(
        text("SELECT 1 FROM users WHERE lower(email) = lower(:e) AND id IS DISTINCT FROM :x"),
        {"e": email, "x": exclude_id},
    ).first() is not None


def insert(conn: Connection, email: str, full_name: str, password_hash: str, role_id: int) -> int:
    return conn.execute(
        text(
            "INSERT INTO users (email, full_name, password_hash, role_id) "
            "VALUES (:email, :name, :ph, :role) RETURNING id"
        ),
        {"email": email, "name": full_name, "ph": password_hash, "role": role_id},
    ).scalar_one()


def update(conn: Connection, user_id: int, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)  # claves controladas por el servicio, no por el cliente
    conn.execute(text(f"UPDATE users SET {sets}, updated_at = now() WHERE id = :id"), {**fields, "id": user_id})


def revoke_tokens(conn: Connection, user_id: int) -> None:
    conn.execute(
        text("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"), {"u": user_id}
    )


def count_active_admins(conn: Connection) -> int:
    """Admin = usuario activo con rol activo y nivel EFECTIVO 4 (override o rol) en todos los módulos."""
    return conn.execute(
        text(
            "SELECT count(*) FROM users u JOIN roles r ON r.id = u.role_id AND r.is_active "
            "WHERE u.is_active AND (SELECT count(*) FROM unnest(CAST(:modules AS text[])) AS m "
            "WHERE coalesce((SELECT level FROM user_permissions WHERE user_id = u.id AND module = m), "
            "(SELECT level FROM role_permissions WHERE role_id = r.id AND module = m), 0) = 4) = :n"
        ),
        {"modules": list(MODULES), "n": len(MODULES)},
    ).scalar_one()


def overrides_for(conn: Connection, user_ids: list[int]) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {uid: {} for uid in user_ids}
    if user_ids:
        rows = conn.execute(
            text("SELECT user_id, module, level FROM user_permissions WHERE user_id = ANY(:ids)"), {"ids": user_ids}
        ).all()
        for uid, module, level in rows:
            result[uid][module] = level
    return result


def set_overrides(conn: Connection, user_id: int, overrides: dict[str, int]) -> None:
    """Reemplaza por completo los overrides del usuario."""
    conn.execute(text("DELETE FROM user_permissions WHERE user_id = :u"), {"u": user_id})
    for module, level in overrides.items():
        conn.execute(
            text("INSERT INTO user_permissions (user_id, module, level) VALUES (:u, :m, :l)"),
            {"u": user_id, "m": module, "l": level},
        )


def lock_admin_changes(conn: Connection) -> None:
    """Serializa operaciones que pueden afectar al conjunto de administradores."""
    conn.execute(text("SELECT pg_advisory_xact_lock(7001)"))
