"""Fixtures de prueba.

Las pruebas usan un PostgreSQL real. El esquema se crea con las migraciones Alembic del repositorio
`consultora-tickets-database` (ruta en DATABASE_REPO_PATH, por defecto ../consultora-tickets-database):
el backend no mantiene una copia del esquema.
"""
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://tickets:change_me_local@127.0.0.1:5432/tickets_test")
DB_REPO = Path(os.environ.get("DATABASE_REPO_PATH", Path(__file__).resolve().parents[2] / "consultora-tickets-database"))

os.environ["DATABASE_URL"] = TEST_URL
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"
os.environ["COOKIE_SECURE"] = "false"
os.environ["CORS_ORIGINS"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.permissions import MODULES  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.auth import limiter  # noqa: E402

PASSWORD = "Password#12345"
_PW_HASH = hash_password(PASSWORD)


def _plain_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def database():
    url = make_url(TEST_URL)
    admin = _plain_url(str(url.set(database="postgres").render_as_string(hide_password=False)))
    with psycopg.connect(admin, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (url.database,)).fetchone():
            conn.execute(f'CREATE DATABASE "{url.database}"')
    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=DB_REPO, env={**os.environ, "DATABASE_URL": TEST_URL}, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Fallaron las migraciones:\n{result.stderr}"
    yield


@pytest.fixture(autouse=True)
def clean(database):
    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        conn.execute(
            "TRUNCATE ticket_events, ticket_comments, tickets, refresh_tokens, users, role_permissions, roles, "
            "clients, ticket_categories RESTART IDENTITY CASCADE"
        )
        conn.execute("ALTER SEQUENCE ticket_folio_seq RESTART")
        conn.execute(
            "INSERT INTO ticket_categories (id, code, name) VALUES (1,'incidente','Incidente'),"
            "(2,'requerimiento','Requerimiento'),(3,'consulta','Consulta')"
        )
    limiter.reset()
    yield


@pytest.fixture
def db():
    with get_engine().begin() as conn:
        yield conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _exec(sql: str, params: tuple = ()):
    """Ejecuta SQL de preparación; devuelve el primer valor si la sentencia tiene RETURNING."""
    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchone()[0] if "RETURNING" in sql else None


@pytest.fixture
def make_role():
    def _make(name: str, active: bool = True, **levels: int) -> int:
        rid = _exec("INSERT INTO roles (name, is_active) VALUES (%s, %s) RETURNING id", (name, active))
        for m in MODULES:
            _exec("INSERT INTO role_permissions (role_id, module, level) VALUES (%s,%s,%s)", (rid, m, levels.get(m, 0)))
        return rid

    return _make


@pytest.fixture
def make_user():
    def _make(email: str, role_id: int, active: bool = True, name: str | None = None) -> int:
        return _exec(
            "INSERT INTO users (email, full_name, password_hash, role_id, is_active) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (email, name or email.split("@")[0].title(), _PW_HASH, role_id, active),
        )

    return _make


@pytest.fixture
def make_client():
    def _make(name: str = "Cliente A", active: bool = True) -> int:
        return _exec("INSERT INTO clients (name, is_active) VALUES (%s,%s) RETURNING id", (name, active))

    return _make


@pytest.fixture
def make_ticket():
    """Inserta un ticket directamente (fechas controladas) con el estado indicado, coherente con las restricciones."""

    def _make(client_id: int, creator_id: int, *, title="Ticket", description="", status="sin_inicio",
              priority="media", category_id=1, assignee_id=None, created_at=None, due_at=None, closed_at=None,
              started_at=None, deleted=False, closed_event=True) -> int:
        created_at = created_at or datetime.now(UTC)
        if status == "cerrado" and closed_at is None:
            closed_at = created_at + timedelta(hours=10)
        tid = _exec(
            "INSERT INTO tickets (title, description, client_id, category_id, priority, status, created_by, "
            "assignee_id, created_at, updated_at, due_at, closed_at, started_at, deleted_at, deleted_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (title, description, client_id, category_id, priority, status, creator_id, assignee_id, created_at,
             created_at, due_at, closed_at, started_at, created_at if deleted else None, creator_id if deleted else None),
        )
        _exec("INSERT INTO ticket_events (ticket_id, event_type, to_status, actor_id, created_at) "
              "VALUES (%s,'created','sin_inicio',%s,%s)", (tid, creator_id, created_at))
        if status == "cerrado" and closed_event:
            _exec("INSERT INTO ticket_events (ticket_id, event_type, from_status, to_status, actor_id, reason, created_at) "
                  "VALUES (%s,'status_change','en_proceso','cerrado',%s,'ok',%s)", (tid, creator_id, closed_at))
        return tid

    return _make


@pytest.fixture
def login(client):
    def _login(email: str, password: str = PASSWORD) -> dict:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login


@pytest.fixture
def world(make_role, make_user, make_client):
    """Entorno base: administrador, agentes y un cliente."""
    admin_role = make_role("Administrador", **{m: 4 for m in MODULES})
    agent_role = make_role("Agente", tickets=3, clients=1, dashboard=1)
    admin = make_user("admin@test.local", admin_role, name="Admin")
    agent1 = make_user("agent1@test.local", agent_role, name="Agente Uno")
    agent2 = make_user("agent2@test.local", agent_role, name="Agente Dos")
    return {
        "admin_role": admin_role, "agent_role": agent_role, "admin": admin, "agent1": agent1, "agent2": agent2,
        "client": make_client("Cliente A"),
    }
