from dataclasses import dataclass, field

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Connection, text

from app.db import get_db
from app.errors import forbidden
from app.permissions import MODULES, Level, is_full_access
from app.security import decode_access_token

bearer = HTTPBearer(auto_error=False, description="Access token JWT obtenido en /api/auth/login")


@dataclass
class CurrentUser:
    id: int
    email: str
    full_name: str
    role_id: int
    role_name: str
    permissions: dict[str, int] = field(default_factory=dict)

    def level(self, module: str) -> int:
        return self.permissions.get(module, 0)

    @property
    def is_admin(self) -> bool:
        return is_full_access(self.permissions)


def _unauthorized() -> HTTPException:
    return HTTPException(401, "Sesión no válida o expirada.", headers={"WWW-Authenticate": "Bearer"})


def load_permissions(conn: Connection, role_id: int) -> dict[str, int]:
    rows = conn.execute(text("SELECT module, level FROM role_permissions WHERE role_id=:r"), {"r": role_id}).all()
    perms = {m: 0 for m in MODULES}
    perms.update({m: lvl for m, lvl in rows})
    return perms


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), conn: Connection = Depends(get_db)
) -> CurrentUser:
    """Autentica y recarga rol/permisos desde la BD en cada solicitud (los cambios aplican de inmediato)."""
    if creds is None:
        raise _unauthorized()
    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise _unauthorized()
    row = conn.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.role_id, r.name AS role_name "
            "FROM users u JOIN roles r ON r.id = u.role_id "
            "WHERE u.id = :id AND u.is_active AND r.is_active"
        ),
        {"id": user_id},
    ).mappings().first()
    if row is None:
        raise _unauthorized()
    return CurrentUser(**row, permissions=load_permissions(conn, row["role_id"]))


def require(module: str, level: Level):
    """Dependencia: exige al menos `level` en `module` (niveles acumulativos)."""

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.level(module) < level:
            raise forbidden()
        return user

    return checker
