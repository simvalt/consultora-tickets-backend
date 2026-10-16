"""Permisos propios por usuario (override por módulo sobre el rol) y matrices en formato JSON."""
import pytest

from app.permissions import effective, parse_permissions


def test_parse_permissions_accepts_names_aliases_and_numbers():
    raw = {"Ticket": "crear", "Analítica": "ver", "usuarios": "Editar", "Permisos": 1, "clientes": "3", "roles": "nada"}
    assert parse_permissions(raw) == {"tickets": 2, "dashboard": 1, "users": 3, "roles": 0, "clients": 3}
    assert parse_permissions({"tickets": "eliminar"}) == {"tickets": 4}


@pytest.mark.parametrize("bad", [{"nada": 1}, {"tickets": "mucho"}, {"tickets": 9}, {"tickets": True}, {"tickets": -1}])
def test_parse_permissions_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_permissions(bad)


def test_effective_override_replaces_role_level_up_or_down():
    role = {"tickets": 3, "users": 0, "roles": 0, "clients": 1, "dashboard": 1}
    eff = effective(role, {"tickets": 1, "users": 2})
    assert eff == {"tickets": 1, "users": 2, "roles": 0, "clients": 1, "dashboard": 1}


def test_example_json_from_requirements_via_api(client, world, login):
    """El ejemplo del cliente: Ticket crear, Analítica ver, Usuario editar, Permisos ver (como JSON)."""
    h = login("admin@test.local")
    body = {
        "email": "nuevo@test.local", "full_name": "Nuevo", "password": "Password#12345", "role_id": world["agent_role"],
        "permission_overrides": {"Ticket": "crear", "Analítica": "ver", "Usuario": "editar", "Permisos": "ver"},
    }
    r = client.post("/api/users", json=body, headers=h)
    assert r.status_code == 201, r.text
    user = r.json()
    assert user["permission_overrides"] == {"tickets": 2, "dashboard": 1, "users": 3, "roles": 1}
    # Efectivos: override sustituye al rol (tickets 3 → 2) y lo no definido hereda (clients 1 del rol Agente).
    assert user["permissions"] == {"tickets": 2, "users": 3, "roles": 1, "clients": 1, "dashboard": 1}


def test_override_applies_immediately_to_access(client, world, login):
    h = login("admin@test.local")
    agent = login("agent1@test.local")
    assert client.get("/api/users", headers=agent).status_code == 403  # el rol Agente no tiene usuarios
    r = client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": {"users": "leer"}}, headers=h)
    assert r.status_code == 200
    assert client.get("/api/users", headers=agent).status_code == 200  # subió con un override
    assert client.get("/api/auth/me", headers=agent).json()["permissions"]["users"] == 1

    # Bajar por debajo del rol: tickets 3 → 1 (sólo lectura).
    client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": {"tickets": 1}}, headers=h)
    me = client.get("/api/auth/me", headers=agent).json()["permissions"]
    assert me["tickets"] == 1 and me["users"] == 0  # el PATCH reemplaza el conjunto de overrides
    assert client.post("/api/tickets", json={"title": "Algo", "client_id": world["client"], "category_id": 1},
                       headers=agent).status_code == 403

    # {} quita todos los overrides y vuelve a heredar del rol.
    client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": {}}, headers=h)
    assert client.get("/api/auth/me", headers=agent).json()["permissions"]["tickets"] == 3
    # Sin el campo no se tocan.
    client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": {"tickets": 2}}, headers=h)
    client.patch(f"/api/users/{world['agent1']}", json={"full_name": "Otro Nombre"}, headers=h)
    assert client.get(f"/api/users/{world['agent1']}", headers=h).json()["permission_overrides"] == {"tickets": 2}


def test_invalid_override_values_are_rejected(client, world, login):
    h = login("admin@test.local")
    for bad in ({"tickets": "mucho"}, {"inventado": 1}, {"tickets": 7}):
        r = client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": bad}, headers=h)
        assert r.status_code == 422, bad


def test_cannot_grant_overrides_above_own_permissions(client, world, make_role, make_user, login):
    make_user("gestor@test.local", make_role("Gestor", users=3, tickets=2))
    h = login("gestor@test.local")
    target = world["agent1"]
    assert client.patch(f"/api/users/{target}", json={"permission_overrides": {"tickets": "eliminar"}},
                        headers=h).status_code == 403
    assert client.patch(f"/api/users/{target}", json={"permission_overrides": {"roles": "ver"}},
                        headers=h).status_code == 403
    # Dentro de lo propio sí (y el agente tenía tickets 3 > 2 del gestor: no puede modificarlo).
    assert client.patch(f"/api/users/{target}", json={"permission_overrides": {"tickets": "leer"}},
                        headers=h).status_code == 403
    other_role = make_role("Basico", tickets=1)
    new = client.post("/api/users", headers=h, json={
        "email": "b@test.local", "full_name": "B", "password": "Password#12345", "role_id": other_role,
        "permission_overrides": {"tickets": "crear"}})
    assert new.status_code == 201
    over = client.post("/api/users", headers=h, json={
        "email": "c@test.local", "full_name": "C", "password": "Password#12345", "role_id": other_role,
        "permission_overrides": {"tickets": "editar"}})
    assert over.status_code == 403


def test_cannot_change_own_overrides(client, world, login):
    h = login("admin@test.local")
    r = client.patch(f"/api/users/{world['admin']}", json={"permission_overrides": {"tickets": 1}}, headers=h)
    assert r.status_code == 403


def test_override_can_make_a_non_admin_role_user_admin_and_counts_for_last_admin(client, world, make_user, login, db):
    """Los overrides cuentan para «administrador»: un usuario de rol Agente con todo en 4 es administrador."""
    from app.db import get_engine
    from app.permissions import MODULES
    from app.repositories import users as users_repo

    assert users_repo.count_active_admins(db) == 1
    h = login("admin@test.local")
    full = {m: "eliminar" for m in MODULES}
    client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": full}, headers=h)
    with get_engine().connect() as conn:
        assert users_repo.count_active_admins(conn) == 2
    # Y un administrador por override puede degradarse sin dejar el sistema sin admin si queda otro.
    assert client.delete(f"/api/users/{world['agent1']}", headers=h).status_code == 200


def test_override_tickets_edit_makes_user_assignable(client, world, make_role, make_user, login):
    h = login("admin@test.local")
    reader = make_user("lector@test.local", make_role("Lector", tickets=1), name="Lector")
    assignees = lambda: {a["id"] for a in client.get("/api/lookups/assignees", headers=h).json()}  # noqa: E731
    assert reader not in assignees()
    client.patch(f"/api/users/{reader}", json={"permission_overrides": {"tickets": "editar"}}, headers=h)
    assert reader in assignees()
    client.patch(f"/api/users/{world['agent1']}", json={"permission_overrides": {"tickets": "leer"}}, headers=h)
    assert world["agent1"] not in assignees()


def test_roles_accept_json_style_permissions(client, world, login):
    h = login("admin@test.local")
    r = client.post("/api/roles", headers=h, json={"name": "JSON", "permissions": {"Ticket": "crear", "Analítica": "ver"}})
    assert r.status_code == 201 and r.json()["permissions"] == {
        "tickets": 2, "users": 0, "roles": 0, "clients": 0, "dashboard": 1}
    r = client.patch(f"/api/roles/{r.json()['id']}", headers=h, json={"permissions": {"Usuario": "editar"}})
    assert r.json()["permissions"]["users"] == 3 and r.json()["permissions"]["tickets"] == 2


def test_lookup_roles_include_permissions_and_listing_shows_effective(client, world, login):
    h = login("admin@test.local")
    roles = client.get("/api/lookups/roles", headers=h).json()
    assert {r["name"]: r["permissions"]["tickets"] for r in roles}["Agente"] == 3
    listing = client.get("/api/users", headers=h).json()["items"]
    agent = next(u for u in listing if u["email"] == "agent1@test.local")
    assert agent["permissions"]["tickets"] == 3 and agent["permission_overrides"] == {}
