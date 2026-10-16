"""QA de seguridad: tokens, sesiones, contraseñas, límite de intentos, validación de entrada y cabeceras."""
import base64
import json
import time
from datetime import UTC, datetime, timedelta

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.services.auth import LoginRateLimiter, limiter
from tests.conftest import PASSWORD, TEST_URL, _plain_url

SECRET = "test-secret-test-secret-test-secret-123456"


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def bearer(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


# ------------------------------------------------------------------ tokens
@pytest.mark.parametrize(
    "token",
    [
        jwt.encode({"sub": "1", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=5)},
                   "otro-secreto-otro-secreto-otro-secreto", algorithm="HS256"),
        jwt.encode({"sub": "1", "iat": datetime.now(UTC) - timedelta(hours=1),
                    "exp": datetime.now(UTC) - timedelta(minutes=1)}, SECRET, algorithm="HS256"),
        jwt.encode({"sub": "1", "exp": datetime.now(UTC) + timedelta(minutes=5)}, SECRET, algorithm="HS256"),
        jwt.encode({"sub": "abc", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=5)},
                   SECRET, algorithm="HS256"),
        jwt.encode({"sub": "1", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=5)},
                   SECRET + SECRET, algorithm="HS512"),
        _b64({"alg": "none", "typ": "JWT"}) + "." + _b64({"sub": "1", "iat": 1, "exp": 9999999999}) + ".",
        "", "a.b.c", "Bearer",
    ],
    ids=["otro-secreto", "expirado", "sin-iat", "sub-no-numerico", "alg-hs512", "alg-none", "vacio", "basura", "palabra"],
)
def test_invalid_tokens_are_rejected(client, world, token):
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_tampered_payload_is_rejected(client, world, login):
    tok = login("agent1@test.local")["Authorization"].split()[1]
    head, payload, sig = tok.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["sub"] = str(world["admin"])  # intento de suplantar al administrador
    assert client.get("/api/auth/me", headers=bearer(f"{head}.{_b64(data)}.{sig}")).status_code == 401


def test_token_for_unknown_user_is_rejected(client, world):
    tok = jwt.encode({"sub": "99999", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=5)},
                     SECRET, algorithm="HS256")
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 401


def test_inactive_role_blocks_login_and_existing_tokens(client, world, login):
    h = login("agent1@test.local")
    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        conn.execute("UPDATE roles SET is_active = false WHERE id = %s", (world["agent_role"],))
    assert client.get("/api/auth/me", headers=h).status_code == 401
    r = client.post("/api/auth/login", json={"email": "agent1@test.local", "password": PASSWORD})
    assert r.status_code == 403


# --------------------------------------------------------- contraseñas
def test_change_own_password_flow(client, world, login):
    old_access = login("agent1@test.local")
    old_cookie = client.cookies.get("refresh_token")
    time.sleep(1.1)  # los tokens se comparan con precisión de segundos

    bad = client.post("/api/auth/change-password", headers=old_access,
                      json={"current_password": "incorrecta", "new_password": "NuevaClave#123"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_current_password"
    same = client.post("/api/auth/change-password", headers=old_access,
                       json={"current_password": PASSWORD, "new_password": PASSWORD})
    assert same.status_code == 400 and same.json()["error"]["code"] == "same_password"
    weak = client.post("/api/auth/change-password", headers=old_access,
                       json={"current_password": PASSWORD, "new_password": "corta"})
    assert weak.status_code == 422

    ok = client.post("/api/auth/change-password", headers=old_access,
                     json={"current_password": PASSWORD, "new_password": "NuevaClave#123"})
    assert ok.status_code == 200
    new_access = bearer(ok.json()["access_token"])
    assert client.get("/api/auth/me", headers=new_access).status_code == 200
    # El access token anterior y el refresh anterior quedan invalidados.
    assert client.get("/api/auth/me", headers=old_access).status_code == 401
    client.cookies.set("refresh_token", old_cookie, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401
    # La nueva contraseña funciona y la anterior no.
    assert client.post("/api/auth/login", json={"email": "agent1@test.local", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"email": "agent1@test.local", "password": "NuevaClave#123"}).status_code == 200


def test_cannot_change_own_password_through_users_endpoint(client, world, login):
    h = login("admin@test.local")
    r = client.patch(f"/api/users/{world['admin']}", json={"password": "OtraClave#12345"}, headers=h)
    assert r.status_code == 403


def test_admin_password_reset_kills_target_sessions(client, world, login):
    agent = login("agent1@test.local")
    time.sleep(1.1)
    admin = login("admin@test.local")
    r = client.patch(f"/api/users/{world['agent1']}", json={"password": "Reseteada#12345"}, headers=admin)
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=agent).status_code == 401


# ------------------------------------------------------- límite de intentos
def test_rate_limit_per_ip_across_many_emails(client, world):
    s = get_settings()
    for i in range(s.login_max_attempts_ip):
        assert client.post("/api/auth/login", json={"email": f"x{i}@t.local", "password": "x"}).status_code == 401
    # Aun con la contraseña correcta de una cuenta real, la IP queda bloqueada.
    r = client.post("/api/auth/login", json={"email": "admin@test.local", "password": PASSWORD})
    assert r.status_code == 429


def test_rate_limit_per_email_across_ips():
    lim = LoginRateLimiter()
    s = get_settings()
    for i in range(s.login_max_attempts_email):
        lim.fail(f"10.0.0.{i}", "victima@x.com")
    with pytest.raises(Exception) as err:
        lim.check("10.9.9.9", "victima@x.com")
    assert getattr(err.value, "status", None) == 429


def test_successful_login_resets_only_its_own_counter(client, world):
    for _ in range(4):
        client.post("/api/auth/login", json={"email": "admin@test.local", "password": "x"})
    assert client.post("/api/auth/login", json={"email": "admin@test.local", "password": PASSWORD}).status_code == 200
    for _ in range(4):
        assert client.post("/api/auth/login", json={"email": "admin@test.local", "password": "x"}).status_code == 401


def test_rate_limiter_memory_is_bounded(monkeypatch):
    lim = LoginRateLimiter()
    monkeypatch.setattr(LoginRateLimiter, "MAX_KEYS", 50)
    monkeypatch.setattr(get_settings(), "login_window_seconds", 0)  # todo caduca de inmediato
    for i in range(200):
        lim.fail(f"ip{i}", f"u{i}@x.com")
    assert len(lim._fails) <= 50 + 3


def test_limiter_fixture_isolated():
    limiter.reset()
    assert limiter._fails == {}


# ------------------------------------------------- validación de entrada
@pytest.mark.parametrize("path,body", [
    ("/api/clients", {"name": "a\x00b"}),
    ("/api/roles", {"name": "r\x00"}),
    ("/api/tickets", {"title": "abc\x00", "client_id": 1, "category_id": 1}),
])
def test_nul_characters_are_rejected_not_500(client, world, login, path, body):
    r = client.post(path, json=body, headers=login("admin@test.local"))
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/api/tickets", "/api/users", "/api/clients", "/api/roles"])
def test_nul_in_query_string_is_not_500(client, world, login, path):
    r = client.get(path, params={"q": "a\x00b"}, headers=login("admin@test.local"))
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"] in ("invalid_data", "validation_error")


@pytest.mark.parametrize("path,body", [
    ("/api/users", {"email": "z@t.local", "full_name": "Z", "password": "Password#12345", "role_id": 1, "is_active": False}),
    ("/api/users", {"email": "z@t.local", "full_name": "Z", "password": "Password#12345", "role_id": 1, "id": 77}),
    ("/api/clients", {"name": "C", "is_active": False}),
    ("/api/roles", {"name": "R", "is_active": False}),
    ("/api/tickets", {"title": "abc", "client_id": 1, "category_id": 1, "status": "cerrado"}),
    ("/api/tickets", {"title": "abc", "client_id": 1, "category_id": 1, "created_by": 2}),
])
def test_mass_assignment_is_rejected(client, world, login, path, body):
    assert client.post(path, json=body, headers=login("admin@test.local")).status_code == 422


@pytest.mark.parametrize("payload", ["' OR 1=1 --", "%", "_", "\\", "'; DROP TABLE tickets; --", "\"", "<script>"])
def test_search_injection_payloads_are_inert(client, world, login, make_ticket, payload):
    make_ticket(world["client"], world["admin"], title="Normal")
    r = client.get("/api/tickets", params={"q": payload}, headers=login("admin@test.local"))
    assert r.status_code == 200 and r.json()["total"] == 0


@pytest.mark.parametrize("params", [
    {"sort": "id; DROP TABLE x"}, {"sort": "password_hash"}, {"order": "sideways"}, {"page": "abc"},
    {"client_id": "1 OR 1=1"}, {"status": "cerrado' --"}, {"created_from": "2026-13-45"}, {"page_size": 0},
])
def test_malformed_query_params_return_422(client, world, login, params):
    assert client.get("/api/tickets", params=params, headers=login("admin@test.local")).status_code == 422


def test_xss_payload_is_stored_verbatim_and_returned_as_json(client, world, login):
    h = login("admin@test.local")
    xss = '<img src=x onerror="alert(1)">'
    r = client.post("/api/tickets", headers=h, json={"title": xss, "description": xss, "client_id": world["client"],
                                                     "category_id": 1})
    assert r.status_code == 201
    got = client.get(f"/api/tickets/{r.json()['id']}", headers=h)
    assert got.headers["content-type"].startswith("application/json") and got.json()["title"] == xss


@pytest.mark.parametrize("path", ["/api/tickets/2147483648", "/api/users/99999999999", "/api/clients/-1",
                                  "/api/roles/0", "/api/tickets/0/events"])
def test_out_of_range_ids_do_not_500(client, world, login, path):
    r = client.get(path, headers=login("admin@test.local"))
    assert r.status_code in (400, 404, 422)


def test_oversized_fields_are_rejected(client, world, login):
    h = login("admin@test.local")
    base = {"client_id": world["client"], "category_id": 1}
    assert client.post("/api/tickets", json={**base, "title": "x" * 201}, headers=h).status_code == 422
    assert client.post("/api/tickets", json={**base, "title": "x" * 200}, headers=h).status_code == 201
    assert client.post("/api/tickets", json={**base, "title": "ok!", "description": "d" * 5001}, headers=h).status_code == 422
    assert client.post("/api/auth/login", json={"email": "a" * 300, "password": "x"}).status_code == 422
    assert client.post("/api/auth/login", json={"email": "a@b.c", "password": "x" * 200}).status_code == 422


def test_malformed_json_body(client, world, login):
    h = {**login("admin@test.local"), "content-type": "application/json"}
    r = client.post("/api/clients", content=b"{not json", headers=h)
    assert r.status_code == 422 and "Traceback" not in r.text


# ---------------------------------------------------------- cabeceras y config
def test_security_headers_on_api(client, world, login):
    r = client.get("/api/users", headers=login("admin@test.local"))
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_refresh_cookie_attributes(client, world):
    r = client.post("/api/auth/login", json={"email": "admin@test.local", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api/auth" in cookie
    assert "refresh_token" not in r.text  # el refresh nunca viaja en el cuerpo


def test_production_settings_refuse_insecure_values():
    with pytest.raises(ValueError):
        Settings(app_env="production", database_url="postgresql://x@y/z",
                 jwt_secret="change-me-local-only-change-me-local-only", cookie_secure=True)
    with pytest.raises(ValueError):
        Settings(app_env="production", database_url="postgresql://x@y/z", jwt_secret="s" * 40, cookie_secure=False)
    ok = Settings(app_env="production", database_url="postgresql://x@y/z", jwt_secret="s" * 40, cookie_secure=True,
                  docs_enabled=None)
    assert ok.show_docs is False  # en producción la documentación interactiva queda oculta salvo que se pida
    assert Settings(app_env="local", database_url="postgresql://x@y/z", jwt_secret="s" * 40,
                    docs_enabled=None).show_docs is True


def test_cors_only_allows_configured_origins(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://tickets.example.com")
    get_settings.cache_clear()
    try:
        from app.main import create_app

        with TestClient(create_app()) as c:
            pre = {"Access-Control-Request-Method": "GET"}
            good = c.options("/api/tickets", headers={"Origin": "https://tickets.example.com", **pre})
            evil = c.options("/api/tickets", headers={"Origin": "https://evil.example", **pre})
            assert good.headers.get("access-control-allow-origin") == "https://tickets.example.com"
            assert "access-control-allow-origin" not in evil.headers
    finally:
        monkeypatch.delenv("CORS_ORIGINS")
        get_settings.cache_clear()


def test_errors_never_leak_internals(client, world, login):
    for r in (client.get("/api/tickets/abc", headers=login("admin@test.local")), client.get("/api/nope"),
              client.post("/api/tickets", json=[], headers=login("admin@test.local"))):
        assert "Traceback" not in r.text and "psycopg" not in r.text and "sqlalchemy" not in r.text.lower()
        assert set(r.json()) == {"error"}
