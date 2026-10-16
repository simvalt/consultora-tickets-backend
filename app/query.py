"""Utilidades compartidas por listados y exportaciones (mismo filtrado, mismo orden)."""
import math
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from sqlalchemy import Connection, text


def like(term: str) -> str:
    """Patrón ILIKE con los comodines del usuario escapados."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass
class PageParams:
    page: int = 1
    page_size: int = 20


def page_params(
    page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100)
) -> PageParams:
    return PageParams(page, page_size)


@dataclass
class Sort:
    sort: str
    order: str  # asc | desc


def order_sql(sort: Sort, columns: dict[str, str], tiebreak: str) -> str:
    """ORDER BY seguro: sólo columnas de la lista blanca."""
    col = columns[sort.sort]
    direction = "DESC" if sort.order == "desc" else "ASC"
    return f"{col} {direction} NULLS LAST, {tiebreak}"


def fetch_page(
    conn: Connection, cols: str, from_sql: str, where: str, binds: dict[str, Any], order: str, p: PageParams
) -> dict[str, Any]:
    total = conn.execute(text(f"SELECT count(*) {from_sql} WHERE {where}"), binds).scalar_one()
    rows = conn.execute(
        text(f"SELECT {cols} {from_sql} WHERE {where} ORDER BY {order} LIMIT :_limit OFFSET :_offset"),
        {**binds, "_limit": p.page_size, "_offset": (p.page - 1) * p.page_size},
    ).mappings().all()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": p.page,
        "page_size": p.page_size,
        "pages": max(1, math.ceil(total / p.page_size)),
    }


def stream_rows(engine, cols: str, from_sql: str, where: str, binds: dict[str, Any], order: str):
    """Generador para exportaciones: recorre TODOS los registros filtrados (sin paginar) con cursor de servidor."""
    with engine.connect() as conn:
        result = conn.execution_options(stream_results=True, max_row_buffer=500).execute(
            text(f"SELECT {cols} {from_sql} WHERE {where} ORDER BY {order}"), binds
        )
        for row in result.mappings():
            yield dict(row)
