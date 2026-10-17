from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from sqlalchemy import Connection

from app.config import get_settings
from app.db import DB
from app.deps import CurrentUser, get_current_user
from app.errors import forbidden
from app.schemas import ChangePasswordIn, LoginIn, MeOut, TokenOut
from app.services import auth as service

router = APIRouter(prefix="/auth", tags=["Autenticación"])

COOKIE = "refresh_token"
COOKIE_PATH = "/api/auth"
CSRF_HEADER = "X-Requested-With"


def _cookie_attrs() -> dict:
    s = get_settings()
    return {"path": COOKIE_PATH, "httponly": True, "secure": s.cookie_secure, "samesite": s.cookie_samesite}


def _set_cookie(response: Response, value: str, expires: datetime) -> None:
    response.set_cookie(COOKIE, value, expires=expires, **_cookie_attrs())


def require_csrf_header(x_requested_with: str | None = Header(default=None, include_in_schema=False)) -> None:
    """Las rutas que actúan sólo con la cookie exigen una cabecera propia.

    Un formulario o enlace de otro sitio no puede añadirla, y desde JavaScript de otro origen obliga a un
    *preflight* CORS que sólo superan los orígenes permitidos. Así nadie puede forzar renovaciones o cierres
    de sesión desde otra página (incluidos otros subdominios del mismo dominio).
    """
    if not x_requested_with:
        raise forbidden("Falta la cabecera X-Requested-With.")


def client_ip(request: Request) -> str:
    """IP del cliente. Detrás de Cloudflare Tunnel se toma de CF-Connecting-IP (CLIENT_IP_HEADER), que el cliente
    no puede falsear porque el backend sólo es alcanzable a través del túnel."""
    header = get_settings().client_ip_header
    if header and (value := request.headers.get(header)):
        return value.strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=TokenOut, summary="Iniciar sesión")
def login(body: LoginIn, request: Request, response: Response, conn: Connection = DB):
    # Los intentos fallidos se cuentan aunque la transacción se revierta (límite en memoria, ver service.login).
    payload, refresh, expires = service.login(conn, body.email, body.password, client_ip(request))
    _set_cookie(response, refresh, expires)
    return payload


@router.post(
    "/refresh", response_model=TokenOut, dependencies=[Depends(require_csrf_header)],
    summary="Renovar el access token con la cookie de refresco (requiere X-Requested-With)",
)
def refresh(response: Response, conn: Connection = DB, refresh_token: str | None = Cookie(default=None)):
    payload, new_raw, expires = service.refresh(conn, refresh_token)
    _set_cookie(response, new_raw, expires)
    return payload


@router.post(
    "/logout", status_code=204, dependencies=[Depends(require_csrf_header)],
    summary="Cerrar sesión: revoca el refresh token (requiere X-Requested-With)",
)
def logout(response: Response, conn: Connection = DB, refresh_token: str | None = Cookie(default=None)):
    service.logout(conn, refresh_token)
    response.delete_cookie(COOKIE, **_cookie_attrs())
    response.status_code = 204
    return response


@router.post("/change-password", response_model=TokenOut, summary="Cambiar la propia contraseña")
def change_password(
    body: ChangePasswordIn, response: Response, user: CurrentUser = Depends(get_current_user), conn: Connection = DB
):
    """Exige la contraseña actual. Cierra todas las demás sesiones y devuelve una nueva."""
    payload, refresh_raw, expires = service.change_own_password(conn, user, body.current_password, body.new_password)
    _set_cookie(response, refresh_raw, expires)
    return payload


@router.get("/me", response_model=MeOut, summary="Usuario autenticado y sus permisos")
def me(user: CurrentUser = Depends(get_current_user)):
    return user.__dict__
