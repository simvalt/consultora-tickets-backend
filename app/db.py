from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def get_db() -> Iterator[Connection]:
    """Una conexión/transacción por solicitud: confirma al terminar o revierte si hay error."""
    with get_engine().begin() as conn:
        yield conn
