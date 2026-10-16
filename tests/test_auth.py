import psycopg

from tests.conftest import PASSWORD, TEST_URL, _plain_url


def test_login_ok_returns_token_user_and_permissions(client, world):
    r = client.post("/api/auth/login", json={"email": "ADMIN@test.local", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert body["user"]["email"] == "admin@test.local"
    assert body["user"]["permissions"]["tickets"] == 4
    assert "password_hash" not in r.text
    cookie = r.headers["set-cookie"]
    assert "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/api/auth" in cookie


def test_login_wrong_password_and_unknown_email_look_the_same(client, world):
    a = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "incorrecta"})
    b = client.post("/api/auth/login", json={"email": "nadie@test.local", "password": "incorrecta"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"


def test_inactive_user_cannot_login(client, world, make_user):
    make_user("off@test.local", world["agent_role"], active=False)
    r = client.post("/api/auth/login", json={"email": "off@test.local", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "user_inactive"


def test_deactivated_user_loses_access_immediately(client, world, login):
    headers = login("agent1@test.local")
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        conn.execute("UPDATE users SET is_active=false WHERE email='agent1@test.local'")
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_me_requires_token_and_rejects_garbage(client, world):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_me_returns_permissions(client, world, login):
    r = client.get("/api/auth/me", headers=login("agent1@test.local"))
    assert r.json()["permissions"] == {"tickets": 3, "users": 0, "roles": 0, "clients": 1, "dashboard": 1}


def test_refresh_rotates_token_and_logout_revokes(client, world):
    client.post("/api/auth/login", json={"email": "admin@test.local", "password": PASSWORD})
    old = client.cookies.get("refresh_token")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200 and r.json()["access_token"]
    new = client.cookies.get("refresh_token")
    assert new and new != old
    # El token anterior ya no sirve (rotación).
    client.cookies.set("refresh_token", old, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401
    client.cookies.set("refresh_token", new, path="/api/auth")
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("refresh_token", new, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401


def test_refresh_without_cookie(client, world):
    assert client.post("/api/auth/refresh").status_code == 401


def test_refresh_denied_for_deactivated_user(client, world):
    client.post("/api/auth/login", json={"email": "agent1@test.local", "password": PASSWORD})
    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        conn.execute("UPDATE users SET is_active=false WHERE email='agent1@test.local'")
    assert client.post("/api/auth/refresh").status_code == 401


def test_login_rate_limit(client, world):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "admin@test.local", "password": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "admin@test.local", "password": PASSWORD})
    assert r.status_code == 429


def test_error_format_is_consistent(client, world):
    r = client.post("/api/auth/login", json={"email": "a"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["details"][0]["field"] == "password"
    assert client.get("/api/nope").json()["error"]["code"] == "not_found"
