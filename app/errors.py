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


# Nombres de campo legibles para los mensajes de validación (el resto se muestra tal cual).
FIELD_LABELS = {
    "title": "Título", "description": "Descripción", "name": "Nombre", "full_name": "Nombre completo",
    "email": "Correo", "password": "Contraseña", "current_password": "Contraseña actual",
    "new_password": "Nueva contraseña", "contact_name": "Contacto", "phone": "Teléfono", "body": "Comentario",
    "reason": "Motivo", "resolution": "Resolución", "due_at": "Vencimiento", "q": "Búsqueda",
    "ticket_id": "Ticket", "user_id": "Usuario", "role_id": "Rol", "client_id": "Cliente",
    "category_id": "Categoría", "assignee_id": "Responsable", "priority": "Prioridad", "status": "Estatus",
    "date_from": "Fecha inicial", "date_to": "Fecha final", "page": "Página", "page_size": "Tamaño de página",
    "permissions": "Permisos", "permission_overrides": "Permisos propios",
}


def _es(err: dict) -> str:
    """Traduce un error de Pydantic a un mensaje en español para el usuario."""
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    if kind == "value_error":
        return str(ctx.get("error") or err.get("msg", "")).removeprefix("Value error, ")
    messages = {
        "missing": "es obligatorio",
        "string_too_short": f"debe tener al menos {ctx.get('min_length')} caracteres",
        "string_too_long": f"admite como máximo {ctx.get('max_length')} caracteres",
        "string_pattern_mismatch": "no tiene un formato válido",
        "int_parsing": "debe ser un número entero",
        "int_type": "debe ser un número entero",
        "int_from_float": "debe ser un número entero",
        "greater_than_equal": f"debe ser mayor o igual a {ctx.get('ge')}",
        "greater_than": f"debe ser mayor que {ctx.get('gt')}",
        "less_than_equal": f"debe ser menor o igual a {ctx.get('le')}",
        "less_than": f"debe ser menor que {ctx.get('lt')}",
        "date_parsing": "no es una fecha válida",
        "date_from_datetime_parsing": "no es una fecha válida",
        "datetime_parsing": "no es una fecha y hora válida",
        "datetime_from_date_parsing": "no es una fecha y hora válida",
        "enum": "no es un valor permitido",
        "literal_error": "no es un valor permitido",
        "bool_parsing": "debe ser verdadero o falso",
        "json_invalid": "no es un JSON válido",
        "too_long": f"admite como máximo {ctx.get('max_length')} elementos",
        "extra_forbidden": "no se admite",
    }
    return messages.get(kind, "no es válido")


def _detail(err: dict) -> dict:
    loc = [str(p) for p in err.get("loc", ())]
    field = ".".join(loc[1:]) or (loc[0] if loc else "")
    label = FIELD_LABELS.get(loc[-1] if loc else "", field)
    message = _es(err)
    # Los value_error propios ya traen frase completa; el resto se antepone con el campo.
    return {"field": field, "message": message if err.get("type") == "value_error" else f"{label}: {message}"}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [_detail(e) for e in exc.errors()]
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
