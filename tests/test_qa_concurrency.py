"""QA de concurrencia: varias solicitudes simultáneas sobre los mismos recursos (servidor real con hilos)."""
import concurrent.futures as cf

import psycopg
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import PASSWORD, TEST_URL, _plain_url


def parallel(n, fn):
    with cf.ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, range(n)))


def _token(email):
    with TestClient(app) as c:
        return {"Authorization": "Bearer " + c.post("/api/auth/login",
                                                   json={"email": email, "password": PASSWORD}).json()["access_token"]}


def test_concurrent_status_changes_only_one_wins(world, make_ticket):
    tid = make_ticket(world["client"], world["admin"], status="iniciado", assignee_id=world["agent1"])
    h = _token("admin@test.local")

    def go(_):
        with TestClient(app) as c:
            return c.post(f"/api/tickets/{tid}/status", json={"status": "en_proceso"}, headers=h).status_code

    codes = parallel(8, go)
    assert sorted(codes) == [200] + [409] * 7  # FOR UPDATE serializa: el resto ve la transición ya hecha
    with psycopg.connect(_plain_url(TEST_URL)) as conn:
        n = conn.execute("SELECT count(*) FROM ticket_events WHERE ticket_id=%s AND event_type='status_change'",
                         (tid,)).fetchone()[0]
    assert n == 1


def test_admins_deactivating_each_other_concurrently_leave_one_admin(world, make_user):
    second = make_user("admin2@test.local", world["admin_role"])
    h1, h2 = _token("admin@test.local"), _token("admin2@test.local")

    def go(i):
        with TestClient(app) as c:
            target, h = (second, h1) if i % 2 == 0 else (world["admin"], h2)
            return c.delete(f"/api/users/{target}", headers=h).status_code

    parallel(2, go)
    with psycopg.connect(_plain_url(TEST_URL)) as conn:
        active = conn.execute("SELECT count(*) FROM users WHERE role_id=%s AND is_active", (world["admin_role"],)).fetchone()[0]
    assert active >= 1


def test_concurrent_unique_names_never_500(world):
    h = _token("admin@test.local")

    def go(_):
        with TestClient(app) as c:
            return c.post("/api/clients", json={"name": "Carrera"}, headers=h).status_code

    codes = parallel(10, go)
    assert codes.count(201) == 1 and set(codes) <= {201, 409}


def test_concurrent_comments_all_persist(world, make_ticket):
    tid = make_ticket(world["client"], world["admin"])
    h = _token("admin@test.local")

    def go(i):
        with TestClient(app) as c:
            return c.post(f"/api/tickets/{tid}/comments", json={"body": f"c{i}"}, headers=h).status_code

    assert parallel(20, go) == [201] * 20
    with TestClient(app) as c:
        assert len(c.get(f"/api/tickets/{tid}/comments", headers=h).json()) == 20


def test_concurrent_refresh_with_same_token_rotates_once(world):
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"email": "admin@test.local", "password": PASSWORD})
        cookie = c.cookies.get("refresh_token")

    def go(_):
        with TestClient(app) as c:
            c.cookies.set("refresh_token", cookie, path="/api/auth")
            return c.post("/api/auth/refresh", headers={"X-Requested-With": "XMLHttpRequest"}).status_code

    codes = parallel(6, go)
    assert codes.count(200) == 1 and codes.count(401) == 5


def test_concurrent_ticket_creation_gets_unique_folios(world):
    h = _token("admin@test.local")

    def go(i):
        with TestClient(app) as c:
            return c.post("/api/tickets", json={"title": f"Paralelo {i}", "client_id": world["client"],
                                                "category_id": 1}, headers=h).json()["folio"]

    folios = parallel(25, go)
    assert len(set(folios)) == 25
