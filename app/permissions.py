"""Módulos y niveles de permiso (escalera acumulativa)."""
from enum import IntEnum


class Level(IntEnum):
    NONE = 0
    READ = 1
    INSERT = 2
    UPDATE = 3
    DELETE = 4


MODULES = ("tickets", "users", "roles", "clients", "dashboard")
MODULE_LABELS = {
    "tickets": "Tickets",
    "users": "Usuarios",
    "roles": "Roles y permisos",
    "clients": "Clientes",
    "dashboard": "Dashboard",
}
LEVEL_LABELS = {0: "Nada", 1: "Leer", 2: "Insertar", 3: "Editar", 4: "Eliminar"}


def is_full_access(perms: dict[str, int]) -> bool:
    return all(perms.get(m, 0) == Level.DELETE for m in MODULES)


def within(actor: dict[str, int], target: dict[str, int]) -> bool:
    """True si `target` no concede más que `actor` en ningún módulo (evita escalada de privilegios)."""
    return all(target.get(m, 0) <= actor.get(m, 0) for m in MODULES)
