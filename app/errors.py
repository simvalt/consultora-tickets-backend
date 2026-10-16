import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("tickets")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: object = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def bad_request(message: str, code: str = "bad_request", details: object = None) -> AppError:
    return AppError(400, code, message, details)


def forbidden(message: str = "No tienes permiso para realizar esta acción.") -> AppError:
    return AppError(403, "forbidden", message)


def not_found(message: str = "El recurso no existe.") -> AppError:
    return AppError(404, "not_found", message)


def conflict(message: str, code: str = "conflict") -> AppError:
    return AppError(409, code, message)


def _body(code: str, message: str, details: object = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(_body("validation_error", "Los datos enviados no son válidos.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        msg = "El recurso no existe." if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(_body(code, msg), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        # Red de seguridad para carreras (p. ej. dos altas simultáneas con el mismo nombre único).
        log.warning("Violación de integridad: %s", exc.orig)
        return JSONResponse(_body("conflict", "El registro entra en conflicto con otro existente."), status_code=409)

    @app.exception_handler(DataError)
    async def _data(_: Request, exc: DataError):
        log.warning("Dato rechazado por la base de datos: %s", exc.orig)
        return JSONResponse(_body("invalid_data", "Los datos enviados no son válidos."), status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("Error no controlado", exc_info=exc)
        return JSONResponse(_body("internal_error", "Ocurrió un error interno."), status_code=500)
