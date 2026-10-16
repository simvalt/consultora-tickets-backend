from typing import Any

from sqlalchemy import Connection, text

from app.permissions import MODULES
from app.query import Sort, like, order_sql

COLS = (
    "r.id, r.name, r.description, r.is_active, r.created_at, r.updated_at, "
    "(SELECT count(*) FROM users u WHERE u.role_id = r.id) AS user_count"
)
FROM = "FROM roles r"
SORT_COLUMNS = {
    "name": "lower(r.name)", "is_active": "r.is_active", "created_at": "r.created_at", "user_count": "user_count",
}


def filters(q: str | None, is_active: bool | None) -> tuple[str, dict[str, Any]]:
    where, binds = ["true"], {}
    if q:
        where.append("(r.name ILIKE :q OR r.description ILIKE :q)")
        binds["q"] = like(q)
    if is_active is not None:
        where.append("r.is_active = :is_active")
        binds["is_active"] = is_active
    return " AND ".join(where), binds


def order(sort: Sort) -> str:
    return order_sql(sort, SORT_COLUMNS, "r.id")


def permissions_for(conn: Connection, role_ids: list[int]) -> dict[int, dict[str, int]]:
    result = {rid: {m: 0 for m in MODULES} for rid in role_ids}
    if role_ids:
        rows = conn.execute(
            text("SELECT role_id, module, level FROM role_permissions WHERE role_id = ANY(:ids)"), {"ids": role_ids}
        ).all()
        for rid, module, level in rows:
            result[rid][module] = level
    return result


def attach_permissions(conn: Connection, roles: list[dict]) -> list[dict]:
    perms = permissions_for(conn, [r["id"] for r in roles])
    for r in roles:
        r["permissions"] = perms[r["id"]]
    return roles


def get(conn: Connection, role_id: int, lock: bool = False) -> dict | None:
    row = conn.execute(
        text(f"SELECT {COLS} {FROM} WHERE r.id = :id" + (" FOR UPDATE OF r" if lock else "")), {"id": role_id}
    ).mappings().first()
    return attach_permissions(conn, [dict(row)])[0] if row else None


def name_taken(conn: Connection, name: str, exclude_id: int | None = None) -> bool:
    return conn.execute(
        text("SELECT 1 FROM roles WHERE lower(name) = lower(:n) AND id IS DISTINCT FROM :x"),
        {"n": name, "x": exclude_id},
    ).first() is not None


def insert(conn: Connection, name: str, description: str) -> int:
    return conn.execute(
        text("INSERT INTO roles (name, description) VALUES (:n, :d) RETURNING id"), {"n": name, "d": description}
    ).scalar_one()


def set_permissions(conn: Connection, role_id: int, perms: dict[str, int]) -> None:
    for module in MODULES:
        conn.execute(
            text(
                "INSERT INTO role_permissions (role_id, module, level) VALUES (:r, :m, :l) "
                "ON CONFLICT (role_id, module) DO UPDATE SET level = EXCLUDED.level"
            ),
            {"r": role_id, "m": module, "l": perms.get(module, 0)},
        )


def update(conn: Connection, role_id: int, fields: dict[str, Any]) -> None:
    fields = {**fields}
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    if fields:
        conn.execute(text(f"UPDATE roles SET {sets}, updated_at = now() WHERE id = :id"), {**fields, "id": role_id})
    else:
        conn.execute(text("UPDATE roles SET updated_at = now() WHERE id = :id"), {"id": role_id})


def active_user_count(conn: Connection, role_id: int) -> int:
    return conn.execute(
        text("SELECT count(*) FROM users WHERE role_id = :r AND is_active"), {"r": role_id}
    ).scalar_one()
