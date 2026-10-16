from typing import Any

from sqlalchemy import Connection, text

from app.query import Sort, like, order_sql

COLS = "c.id, c.name, c.contact_name, c.email, c.phone, c.is_active, c.created_at, c.updated_at"
FROM = "FROM clients c"
SORT_COLUMNS = {
    "name": "lower(c.name)", "contact_name": "lower(c.contact_name)", "email": "lower(c.email)",
    "is_active": "c.is_active", "created_at": "c.created_at",
}


def filters(q: str | None, is_active: bool | None) -> tuple[str, dict[str, Any]]:
    where, binds = ["true"], {}
    if q:
        where.append("(c.name ILIKE :q OR c.contact_name ILIKE :q OR c.email ILIKE :q)")
        binds["q"] = like(q)
    if is_active is not None:
        where.append("c.is_active = :is_active")
        binds["is_active"] = is_active
    return " AND ".join(where), binds


def order(sort: Sort) -> str:
    return order_sql(sort, SORT_COLUMNS, "c.id")


def get(conn: Connection, client_id: int) -> dict | None:
    row = conn.execute(text(f"SELECT {COLS} {FROM} WHERE c.id = :id"), {"id": client_id}).mappings().first()
    return dict(row) if row else None


def name_taken(conn: Connection, name: str, exclude_id: int | None = None) -> bool:
    return conn.execute(
        text("SELECT 1 FROM clients WHERE lower(name) = lower(:n) AND id IS DISTINCT FROM :x"),
        {"n": name, "x": exclude_id},
    ).first() is not None


def insert(conn: Connection, data: dict[str, Any]) -> int:
    return conn.execute(
        text(
            "INSERT INTO clients (name, contact_name, email, phone) VALUES (:name, :contact_name, :email, :phone) "
            "RETURNING id"
        ),
        data,
    ).scalar_one()


def update(conn: Connection, client_id: int, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    conn.execute(text(f"UPDATE clients SET {sets}, updated_at = now() WHERE id = :id"), {**fields, "id": client_id})
