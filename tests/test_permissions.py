"""Escalera de permisos, anti-escalada y protección del último administrador."""
import pytest

from app.deps import CurrentUser
from app.errors import AppError
from app.permissions import MODULES
from app.schemas import UserUpdate
from app.services import users as users_service

LADDER = {"none": 0, "read": 1, "insert": 2, "update": 3, "delete": 4}


@pytest.fixture
def ladder(world, make_role, make_user):
    """Un usuario por nivel (0-4) para cada módulo, todos sobre el mismo módulo."""

    def _for(module: str) -> dict[int, str]:
        users = {}
        for level in range(5):
            rid = make_role(f"{module}-{level}", **{module: level})
            email = f"{module}{level}@test.local"
            make_user(email, rid)
            users[level] = email
        return users

    return _for


def test_unauthenticated_requests_are_rejected(client, world):
    for path in ("/api/tickets", "/api/users", "/api/roles", "/api/clients", "/api/dashboard", "/api/tickets/export"):
        assert client.get(path).status_code == 401


@pytest.mark.parametrize("level,expected", [(0, 403), (1, 200), (2, 200), (3, 200), (4, 200)])
@pytest.mark.parametrize(
    "module,path",
    [("tickets", "/api/tickets"), ("users", "/api/users"), ("roles", "/api/roles"), ("clients", "/api/clients"),
     ("dashboard", "/api/dashboard")],
)
def test_read_and_export_need_read_level(client, ladder, login, module, path, level, expected):
    headers = login(ladder(module)[level])
    assert client.get(path, headers=headers).status_code == expected
    if module != "dashboard":
        assert client.get(path + "/export", headers=headers).status_code == expected
    else:
        assert client.get(path + "/export", headers=headers).status_code == expected


@pytest.mark.parametrize("level", range(5))
def test_ticket_ladder(client, world, ladder, login, make_ticket, level):
    users = ladder("tickets")
    headers = login(users[level])
    tid = make_ticket(world["client"], world["admin"], status="en_proceso", assignee_id=world["agent1"])
    payload = {"title": "Nuevo ticket", "client_id": world["client"], "category_id": 1}

    can = lambda needed: level >= needed  # noqa: E731
    assert (client.post("/api/tickets", json=payload, headers=headers).status_code == 201) == can(2)
    assert (client.patch(f"/api/tickets/{tid}", json={"title": "Otro título"}, headers=headers).status_code == 200) == can(3)
    r = client.post(f"/api/tickets/{tid}/comments", json={"body": "hola"}, headers=headers)
    assert (r.status_code == 201) == can(3)
    r = client.post(f"/api/tickets/{tid}/status", json={"status": "pendiente", "reason": "x"}, headers=headers)
    assert (r.status_code == 200) == can(3)
    r = client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": world["agent2"]}, headers=headers)
    assert (r.status_code == 200) == can(3)
    assert (client.delete(f"/api/tickets/{tid}", headers=headers).status_code == 204) == can(4)


@pytest.mark.parametrize("level", range(5))
def test_client_ladder(client, ladder, login, level):
    headers = login(ladder("clients")[level])
    r = client.post("/api/clients", json={"name": f"Cliente {level}"}, headers=headers)
    assert (r.status_code == 201) == (level >= 2)
    if level >= 2:
        cid = r.json()["id"]
        assert (client.patch(f"/api/clients/{cid}", json={"phone": "1"}, headers=headers).status_code == 200) == (level >= 3)
        assert (client.delete(f"/api/clients/{cid}", headers=headers).status_code == 200) == (level >= 4)


def test_user_cannot_assign_role_with_more_privileges(client, world, make_role, make_user, login):
    manager_role = make_role("Gestor", users=3, clients=1)
    make_user("gestor@test.local", manager_role)
    headers = login("gestor@test.local")
    body = {"email": "nuevo@test.local", "full_name": "Nuevo", "password": "Password#12345"}
    r = client.post("/api/users", json={**body, "role_id": world["admin_role"]}, headers=headers)
    assert r.status_code == 403
    # Con un rol de igual alcance que el suyo sí puede.
    r = client.post("/api/users", json={**body, "role_id": manager_role}, headers=headers)
    assert r.status_code == 201


def test_user_cannot_promote_self_or_modify_superior(client, world, make_role, make_user, login):
    manager_role = make_role("Gestor", users=3)
    mid = make_user("gestor@test.local", manager_role)
    headers = login("gestor@test.local")
    assert client.patch(f"/api/users/{mid}", json={"role_id": world["admin_role"]}, headers=headers).status_code == 403
    # No puede editar ni resetear la contraseña del administrador.
    r = client.patch(f"/api/users/{world['admin']}", json={"password": "OtraClave#12345"}, headers=headers)
    assert r.status_code == 403
    assert client.delete(f"/api/users/{world['admin']}", headers=headers).status_code == 403


def test_role_grants_are_capped_by_own_permissions(client, world, make_role, make_user, login):
    role_admin = make_role("Gestor de roles", roles=4, tickets=2)
    make_user("roles@test.local", role_admin)
    headers = login("roles@test.local")
    r = client.post("/api/roles", json={"name": "X", "permissions": {"tickets": 4}}, headers=headers)
    assert r.status_code == 403
    r = client.post("/api/roles", json={"name": "Y", "permissions": {"tickets": 2, "roles": 1}}, headers=headers)
    assert r.status_code == 201
    # No puede modificar su propio rol ni uno superior.
    assert client.patch(f"/api/roles/{role_admin}", json={"permissions": {"tickets": 0}}, headers=headers).status_code == 403
    assert client.patch(f"/api/roles/{world['admin_role']}", json={"name": "Hack"}, headers=headers).status_code == 403


def test_role_matrix_validation(client, world, login):
    headers = login("admin@test.local")
    assert client.post("/api/roles", json={"name": "Z", "permissions": {"tickets": 9}}, headers=headers).status_code == 422
    assert client.post("/api/roles", json={"name": "Z", "permissions": {"nada": 1}}, headers=headers).status_code == 422
    ok = client.post("/api/roles", json={"name": "Z", "permissions": {"tickets": 2}}, headers=headers)
    assert ok.status_code == 201 and ok.json()["permissions"]["tickets"] == 2 and set(ok.json()["permissions"]) == set(MODULES)
    dup = client.post("/api/roles", json={"name": "z"}, headers=headers)
    assert dup.status_code == 409


def test_admin_can_manage_roles_and_users_end_to_end(client, world, login):
    headers = login("admin@test.local")
    role = client.post("/api/roles", json={"name": "Soporte", "permissions": {"tickets": 3, "dashboard": 1}}, headers=headers).json()
    user = client.post(
        "/api/users",
        json={"email": "nuevo@test.local", "full_name": "Nuevo", "password": "Password#12345", "role_id": role["id"]},
        headers=headers,
    )
    assert user.status_code == 201
    new_headers = login("nuevo@test.local")
    assert client.get("/api/tickets", headers=new_headers).status_code == 200
    assert client.get("/api/users", headers=new_headers).status_code == 403
    # Cambiar permisos del rol surte efecto de inmediato.
    client.patch(f"/api/roles/{role['id']}", json={"permissions": {"tickets": 0}}, headers=headers)
    assert client.get("/api/tickets", headers=new_headers).status_code == 403


def test_duplicate_email_and_weak_password(client, world, login):
    headers = login("admin@test.local")
    body = {"email": "ADMIN@test.local", "full_name": "Dup", "password": "Password#12345", "role_id": world["agent_role"]}
    assert client.post("/api/users", json=body, headers=headers).status_code == 409
    body.update(email="otro@test.local", password="corta")
    assert client.post("/api/users", json=body, headers=headers).status_code == 422


def test_cannot_deactivate_yourself_or_change_own_role(client, world, login):
    headers = login("admin@test.local")
    r = client.delete(f"/api/users/{world['admin']}", headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "self_deactivation"
    r = client.patch(f"/api/users/{world['admin']}", json={"role_id": world["agent_role"]}, headers=headers)
    assert r.status_code == 403


def test_role_with_active_users_cannot_be_deactivated(client, world, login):
    headers = login("admin@test.local")
    r = client.delete(f"/api/roles/{world['agent_role']}", headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "role_in_use"


def test_admin_can_demote_a_second_admin_but_one_always_remains(client, world, make_role, make_user, login):
    second = make_user("admin2@test.local", world["admin_role"])
    headers = login("admin@test.local")
    assert client.patch(f"/api/users/{second}", json={"role_id": world["agent_role"]}, headers=headers).status_code == 200
    # El único administrador restante no puede desactivarse ni degradarse a sí mismo.
    assert client.delete(f"/api/users/{world['admin']}", headers=headers).status_code == 409
    assert client.patch(f"/api/users/{world['admin']}", json={"role_id": world["agent_role"]}, headers=headers).status_code == 403


def test_last_admin_guard_blocks_operations_that_leave_no_admin(world, db):
    """Defensa en profundidad: aunque quien actúa tuviera permisos totales, el servicio no deja el sistema sin admin."""
    forged = CurrentUser(id=999, email="x", full_name="x", role_id=world["admin_role"], role_name="x",
                         permissions={m: 4 for m in MODULES})
    with pytest.raises(AppError) as err:
        users_service.deactivate(db, forged, world["admin"])
    assert err.value.code == "last_admin"
    with pytest.raises(AppError) as err:
        users_service.update(db, forged, world["admin"], UserUpdate(role_id=world["agent_role"]))
    assert err.value.code == "last_admin"
