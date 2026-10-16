from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy import Connection

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.schemas import LoginIn, MeOut, TokenOut
from app.services import auth as service

router = APIRouter(prefix="/auth", tags=["Autenticación"])

COOKIE = "refresh_token"
COOKIE_PATH = "/api/auth"


def _set_cookie(response: Response, value: str, expires: datetime) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE, value, expires=expires, path=COOKIE_PATH, httponly=True, secure=s.cookie_secure,
        samesite=s.cookie_samesite,  # type: ignore[arg-type]
    )


@router.post("/login", response_model=TokenOut, summary="Iniciar sesión")
def login(body: LoginIn, request: Request, response: Response, conn: Connection = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    # Los intentos fallidos deben contarse aunque la transacción se revierta: ver service.login (límite en memoria).
    payload, refresh, expires = service.login(conn, body.email, body.password, ip)
    _set_cookie(response, refresh, expires)
    response.headers["Cache-Control"] = "no-store"
    return payload


@router.post("/refresh", response_model=TokenOut, summary="Renovar el access token con la cookie de refresco")
def refresh(
    response: Response, conn: Connection = Depends(get_db), refresh_token: str | None = Cookie(default=None)
):
    payload, new_raw, expires = service.refresh(conn, refresh_token)
    _set_cookie(response, new_raw, expires)
    response.headers["Cache-Control"] = "no-store"
    return payload


@router.post("/logout", status_code=204, summary="Cerrar sesión (revoca el refresh token)")
def logout(response: Response, conn: Connection = Depends(get_db), refresh_token: str | None = Cookie(default=None)):
    service.logout(conn, refresh_token)
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut, summary="Usuario autenticado y sus permisos")
def me(user: CurrentUser = Depends(get_current_user)):
    return user.__dict__
