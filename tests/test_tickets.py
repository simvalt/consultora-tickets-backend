"""Flujo de estatus, historial, reglas de motivo/resolución y eliminación lógica."""
import itertools

import psycopg
import pytest

from app import workflow as wf
from tests.conftest import TEST_URL, _plain_url


@pytest.fixture
def api(client, world, login):
    h = login("admin@test.local")

    class Api:
        def create(self, **kw):
            body = {"title": "Falla en servidor", "description": "Detalle", "client_id": world["client"], "category_id": 1, **kw}
            r = client.post("/api/tickets", json=body, headers=h)
            assert r.status_code == 201, r.text
            return r.json()

        def status(self, tid, status, **kw):
            return client.post(f"/api/tickets/{tid}/status", json={"status": status, **kw}, headers=h)

        def events(self, tid):
            return client.get(f"/api/tickets/{tid}/events", headers=h).json()

        def get(self, tid):
            return client.get(f"/api/tickets/{tid}", headers=h)

    return Api()


def test_create_ticket_defaults_and_folio(api):
    t = api.create()
    assert t["status"] == "sin_inicio" and t["folio"] == "TCK-000001"
    assert t["assignee_id"] is None and t["started_at"] is None and t["closed_at"] is None
    assert t["allowed_transitions"] == ["iniciado", "cancelado", "rechazado"]
    assert api.create()["folio"] == "TCK-000002"
    ev = api.events(t["id"])
    assert [e["event_type"] for e in ev] == ["created"]


def test_full_lifecycle_with_history(api, world):
    t = api.create(priority="alta")
    tid = t["id"]
    assert api.status(tid, "iniciado", assignee_id=world["agent1"]).status_code == 200
    started = api.get(tid).json()["started_at"]
    assert started is not None
    assert api.status(tid, "en_proceso").status_code == 200
    assert api.status(tid, "pendiente", reason="Esperando al cliente").status_code == 200
    assert api.status(tid, "en_proceso").status_code == 200
    closed = api.status(tid, "cerrado", resolution="Se reinició el servicio").json()
    assert closed["status"] == "cerrado" and closed["closed_at"] and closed["resolution"] == "Se reinició el servicio"
    assert closed["allowed_transitions"] == ["en_proceso"]

    # Reapertura: exige motivo, limpia el cierre vigente y conserva el inicio original.
    assert api.status(tid, "en_proceso").status_code == 400
    reopened = api.status(tid, "en_proceso", reason="Reapareció el fallo").json()
    assert reopened["closed_at"] is None and reopened["resolution"] is None and reopened["started_at"] == started
    assert api.status(tid, "cerrado", resolution="Solución definitiva").status_code == 200

    types = [(e["event_type"], e["from_status"], e["to_status"]) for e in api.events(tid)]
    assert types == [
        ("created", None, "sin_inicio"),
        ("assignment", None, None),
        ("status_change", "sin_inicio", "iniciado"),
        ("status_change", "iniciado", "en_proceso"),
        ("status_change", "en_proceso", "pendiente"),
        ("status_change", "pendiente", "en_proceso"),
        ("status_change", "en_proceso", "cerrado"),
        ("reopen", "cerrado", "en_proceso"),
        ("status_change", "en_proceso", "cerrado"),
    ]
    events = api.events(tid)
    assert events[4]["reason"] == "Esperando al cliente" and events[7]["reason"] == "Reapareció el fallo"
    assert events[2]["actor_name"] == "Admin"
    assert events[1]["to_assignee_id"] == world["agent1"]


ALL = wf.STATUSES
INVALID = [(a, b) for a, b in itertools.product(ALL, ALL) if not wf.can_transition(a, b)]


def _put_in_status(make_ticket, world, status):
    assignee = None if status in ("sin_inicio", "cancelado", "rechazado") else world["agent1"]
    return make_ticket(world["client"], world["admin"], status=status, assignee_id=assignee)


@pytest.mark.parametrize("current,new", INVALID)
def test_invalid_transitions_are_rejected_and_leave_no_trace(api, world, make_ticket, current, new):
    tid = _put_in_status(make_ticket, world, current)
    before = len(api.events(tid))
    r = api.status(tid, new, reason="motivo", resolution="resolución", assignee_id=world["agent1"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    assert api.get(tid).json()["status"] == current
    assert len(api.events(tid)) == before


@pytest.mark.parametrize("current,new", [(a, b) for a in ALL for b in wf.TRANSITIONS[a]])
def test_every_valid_transition_works_with_required_data(api, world, make_ticket, current, new):
    tid = _put_in_status(make_ticket, world, current)
    r = api.status(tid, new, reason="Motivo válido", resolution="Resumen de resolución", assignee_id=world["agent1"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == new


@pytest.mark.parametrize("current,new", [("sin_inicio", "cancelado"), ("sin_inicio", "rechazado"),
                                         ("iniciado", "pendiente"), ("en_proceso", "cancelado"), ("pendiente", "cancelado")])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_reason_is_mandatory(api, world, make_ticket, current, new, blank):
    tid = _put_in_status(make_ticket, world, current)
    r = api.status(tid, new, reason=blank, assignee_id=world["agent1"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "reason_required"
    assert api.get(tid).json()["status"] == current


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_closing_requires_resolution_summary(api, world, make_ticket, blank):
    tid = _put_in_status(make_ticket, world, "en_proceso")
    r = api.status(tid, "cerrado", resolution=blank, reason="algo")
    assert r.status_code == 400 and r.json()["error"]["code"] == "resolution_required"


def test_starting_requires_an_assignee(api):
    t = api.create()
    r = api.status(t["id"], "iniciado")
    assert r.status_code == 400 and r.json()["error"]["code"] == "assignee_required"
    assert api.get(t["id"]).json()["status"] == "sin_inicio"


def test_starting_with_pre_assigned_ticket_and_invalid_assignee(api, world, make_user, make_role):
    t = api.create(assignee_id=world["agent1"])
    assert api.status(t["id"], "iniciado").status_code == 200
    viewer = make_user("lector@test.local", make_role("Lector", tickets=1))
    t2 = api.create()
    r = api.status(t2["id"], "iniciado", assignee_id=viewer)  # sin permiso de edición: no puede atender
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_assignee"


def test_assign_endpoint(client, world, login, api):
    h = login("admin@test.local")
    t = api.create()
    tid = t["id"]
    r = client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": world["agent1"]}, headers=h)
    assert r.status_code == 200 and r.json()["assignee_name"] == "Agente Uno"
    r = client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": world["agent2"]}, headers=h)
    assert r.json()["assignee_id"] == world["agent2"]
    r = client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": None}, headers=h)  # sin_inicio puede quedar libre
    assert r.status_code == 200 and r.json()["assignee_id"] is None
    assert client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": 99999}, headers=h).status_code == 400
    api.status(tid, "iniciado", assignee_id=world["agent1"])
    r = client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": None}, headers=h)
    assert r.status_code == 409  # en atención debe conservar responsable
    api.status(tid, "en_proceso")
    api.status(tid, "cerrado", resolution="ok")
    r = client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": world["agent2"]}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ticket_finished"
    kinds = [(e["event_type"], e["from_assignee_id"], e["to_assignee_id"]) for e in api.events(tid) if e["event_type"] == "assignment"]
    assert kinds == [
        ("assignment", None, world["agent1"]),
        ("assignment", world["agent1"], world["agent2"]),
        ("assignment", world["agent2"], None),
        ("assignment", None, world["agent1"]),
    ]


def test_started_at_is_only_set_the_first_time(api, world):
    t = api.create()
    first = api.status(t["id"], "iniciado", assignee_id=world["agent1"]).json()["started_at"]
    api.status(t["id"], "en_proceso")
    api.status(t["id"], "cerrado", resolution="ok")
    api.status(t["id"], "en_proceso", reason="de nuevo")
    assert api.get(t["id"]).json()["started_at"] == first


def test_comments_have_author_and_date_and_validate(client, world, login, api):
    h = login("agent1@test.local")
    tid = api.create()["id"]
    r = client.post(f"/api/tickets/{tid}/comments", json={"body": "  Revisado el log  "}, headers=h)
    assert r.status_code == 201
    assert r.json()["body"] == "Revisado el log" and r.json()["author_name"] == "Agente Uno" and r.json()["created_at"]
    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "   "}, headers=h).status_code == 422
    assert len(client.get(f"/api/tickets/{tid}/comments", headers=h).json()) == 1
    assert client.post("/api/tickets/9999/comments", json={"body": "x"}, headers=h).status_code == 404


def test_soft_delete_hides_ticket_but_keeps_history(client, world, login, api):
    h = login("admin@test.local")
    tid = api.create()["id"]
    api.status(tid, "cancelado", reason="Duplicado")
    assert client.delete(f"/api/tickets/{tid}", headers=h).status_code == 204
    assert api.get(tid).status_code == 404
    assert client.get("/api/tickets", headers=h).json()["total"] == 0
    assert client.delete(f"/api/tickets/{tid}", headers=h).status_code == 404
    with psycopg.connect(_plain_url(TEST_URL)) as conn:
        assert conn.execute("SELECT deleted_at IS NOT NULL FROM tickets WHERE id=%s", (tid,)).fetchone()[0]
        kinds = [r[0] for r in conn.execute("SELECT event_type FROM ticket_events WHERE ticket_id=%s ORDER BY id", (tid,))]
    assert kinds == ["created", "status_change", "deleted"]


def test_edit_ticket_fields(client, world, login, api):
    h = login("admin@test.local")
    tid = api.create(due_at="2030-01-01T10:00:00Z")["id"]
    r = client.patch(f"/api/tickets/{tid}", json={"title": "Nuevo título", "priority": "critica", "due_at": None}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Nuevo título" and body["priority"] == "critica" and body["due_at"] is None
    assert client.patch(f"/api/tickets/{tid}", json={"title": "ab"}, headers=h).status_code == 422
    assert client.patch(f"/api/tickets/{tid}", json={"client_id": 999}, headers=h).status_code == 400
    assert client.patch(f"/api/tickets/{tid}", json={"status": "cerrado"}, headers=h).json()["status"] == "sin_inicio"


def test_create_validations(client, world, login, make_client):
    h = login("admin@test.local")
    base = {"title": "Un ticket", "client_id": world["client"], "category_id": 1}
    assert client.post("/api/tickets", json={**base, "client_id": 999}, headers=h).status_code == 400
    assert client.post("/api/tickets", json={**base, "category_id": 99}, headers=h).status_code == 400
    assert client.post("/api/tickets", json={**base, "priority": "urgente"}, headers=h).status_code == 422
    assert client.post("/api/tickets", json={**base, "title": ""}, headers=h).status_code == 422
    inactive = make_client("Inactivo", active=False)
    assert client.post("/api/tickets", json={**base, "client_id": inactive}, headers=h).status_code == 400


def test_assigning_on_create_needs_edit_permission(client, world, make_role, make_user, login):
    make_user("cap@test.local", make_role("Capturista", tickets=2))
    h = login("cap@test.local")
    base = {"title": "Un ticket", "client_id": world["client"], "category_id": 1}
    assert client.post("/api/tickets", json=base, headers=h).status_code == 201
    assert client.post("/api/tickets", json={**base, "assignee_id": world["agent1"]}, headers=h).status_code == 403


def test_status_change_is_atomic_with_its_history(client, world, login, api, monkeypatch):
    """Si falla el registro del historial, el cambio de estatus se revierte (misma transacción)."""
    from app.repositories import tickets as repo

    tid = api.create(assignee_id=world["agent1"])["id"]

    def boom(*a, **k):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(repo, "add_event", boom)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post(f"/api/tickets/{tid}/status", json={"status": "iniciado"}, headers=login("admin@test.local"))
    assert r.status_code == 500 and "Traceback" not in r.text and r.json()["error"]["code"] == "internal_error"
    monkeypatch.undo()
    assert api.get(tid).json()["status"] == "sin_inicio"
