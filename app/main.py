from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.requests import Request

from app.config import get_settings
from app.db import get_engine
from app.errors import install_handlers
from app.routers import auth, clients, dashboard, meta, roles, tickets, users


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Consultora Tickets API",
        version="1.0.0",
        description="API del sistema interno de tickets. Autenticación: Bearer JWT (ver /api/auth/login).",
        docs_url="/api/docs" if settings.show_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.show_docs else None,
    )
    if settings.cors_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
            expose_headers=["Content-Disposition"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/"):
            # Respuestas con datos de usuarios autenticados: nunca en cachés intermedias ni del navegador.
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    install_handlers(app)

    api = APIRouter(prefix="/api")
    for module in (auth, users, roles, clients, tickets, dashboard, meta):
        api.include_router(module.router)

    @api.get("/health", tags=["Salud"], summary="Comprobación de salud (incluye la base de datos)")
    def health():
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(api)
    return app


app = create_app()
