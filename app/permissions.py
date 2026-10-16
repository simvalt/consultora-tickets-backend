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


# ---- Lectura flexible de matrices de permisos (JSON) ----
LEVEL_ALIASES = {
    "nada": 0, "ninguno": 0, "none": 0,
    "leer": 1, "ver": 1, "consultar": 1, "read": 1,
    "insertar": 2, "crear": 2, "insert": 2, "create": 2,
    "editar": 3, "modificar": 3, "update": 3, "edit": 3,
    "eliminar": 4, "borrar": 4, "delete": 4,
}
MODULE_ALIASES = {
    "ticket": "tickets", "tickets": "tickets",
    "usuario": "users", "usuarios": "users", "users": "users", "user": "users",
    "rol": "roles", "roles": "roles", "permisos": "roles", "permiso": "roles",
    "cliente": "clients", "clientes": "clients", "clients": "clients",
    "dashboard": "dashboard", "analitica": "dashboard", "analítica": "dashboard",
}


def parse_permissions(raw: dict) -> dict[str, int]:
    """Normaliza {"Ticket": "crear", "analitica": 1, ...} a {"tickets": 2, "dashboard": 1}.

    Acepta niveles numéricos (0-4) o sus nombres en español/inglés y alias de módulo.
    """
    result: dict[str, int] = {}
    for key, value in raw.items():
        module = MODULE_ALIASES.get(str(key).strip().lower())
        if module is None:
            raise ValueError(f"Módulo desconocido: {key}")
        if isinstance(value, bool):
            raise ValueError(f"Nivel no válido para {key}: {value}")
        if isinstance(value, int):
            level = value
        elif isinstance(value, str) and value.strip().lower() in LEVEL_ALIASES:
            level = LEVEL_ALIASES[value.strip().lower()]
        elif isinstance(value, str) and value.strip().isdigit():
            level = int(value.strip())
        else:
            raise ValueError(f"Nivel no válido para {key}: {value}")
        if not 0 <= level <= 4:
            raise ValueError("El nivel debe estar entre 0 y 4")
        result[module] = level
    return result


def effective(role_perms: dict[str, int], overrides: dict[str, int]) -> dict[str, int]:
    """Permisos efectivos: el override del usuario sustituye al nivel del rol por módulo."""
    return {m: overrides.get(m, role_perms.get(m, 0)) for m in MODULES}
