"""QA funcional: casos límite de listados, fechas, unicode, dashboard y reglas de negocio."""
import csv
import io
from datetime import UTC, date, datetime, timedelta

import pytest

NOW = datetime.now(UTC)


@pytest.fixture
def many(world, make_ticket):
    """30 tickets con valores repetidos (para comprobar que el orden es estable entre páginas)."""
    for i in range(30):
        make_ticket(world["client"], world["admin"], title=f"Igual {i % 3}", priority=("baja", "alta")[i % 2],
                    created_at=NOW - timedelta(hours=i % 4))
    return world


@pytest.mark.parametrize("sort", ["folio", "title", "client", "category", "priority", "status", "assignee",
                                  "created_at", "updated_at", "due_at"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_every_sort_paginates_without_gaps_or_duplicates(client, many, login, sort, order):
    h = login("admin@test.local")
    seen = []
    for page in range(1, 5):
        body = client.get("/api/tickets", params={"sort": sort, "order": order, "page": page, "page_size": 8},
                          headers=h).json()
        seen += [t["id"] for t in body["items"]]
    assert len(seen) == 30 and len(set(seen)) == 30  # desempate por id: ni repetidos ni perdidos
    full = client.get("/api/tickets", params={"sort": sort, "order": order, "page_size": 100}, headers=h).json()
    assert [t["id"] for t in full["items"]] == seen


@pytest.mark.parametrize("path,sort", [("/api/users", "full_name"), ("/api/users", "last_login_at"),
                                       ("/api/roles", "user_count"), ("/api/clients", "email")])
def test_other_listings_sort_and_paginate(client, world, login, path, sort):
    h = login("admin@test.local")
    for order in ("asc", "desc"):
        r = client.get(path, params={"sort": sort, "order": order, "page_size": 1}, headers=h)
        assert r.status_code == 200 and r.json()["page_size"] == 1


def test_csv_streams_more_than_one_chunk(client, world, make_ticket, login):
    """La exportación escribe por bloques de 500 filas: con 1 203 se cruzan dos fronteras de bloque."""
    import psycopg

    from tests.conftest import TEST_URL, _plain_url

    with psycopg.connect(_plain_url(TEST_URL), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO tickets (title, client_id, category_id, created_by) "
            "SELECT 'Masivo ' || g, %s, 1, %s FROM generate_series(1, 1203) g",
            (world["client"], world["admin"]),
        )
    r = client.get("/api/tickets/export", params={"sort": "folio", "order": "asc"}, headers=login("admin@test.local"))
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"), newline="")))
    assert len(rows) == 1 + 1203
    assert rows[1][0] == "TCK-000001" and rows[-1][0] == "TCK-001203"
    assert len({row[0] for row in rows[1:]}) == 1203


def test_unicode_and_emoji_round_trip_and_search(client, world, login):
    h = login("admin@test.local")
    title = "Fallo en facturación 🚨 — ñandú «comillas» 漢字"
    r = client.post("/api/tickets", headers=h, json={"title": title, "client_id": world["client"], "category_id": 1})
    assert r.json()["title"] == title
    assert client.get("/api/tickets", params={"q": "ÑANDÚ"}, headers=h).json()["total"] == 1  # sin distinguir mayúsculas
    assert client.get("/api/tickets", params={"q": "🚨"}, headers=h).json()["total"] == 1
    text = client.get("/api/tickets/export", headers=h).content.decode("utf-8-sig")
    assert title in text


def test_search_trims_and_matches_folio_partially(client, world, make_ticket, login):
    make_ticket(world["client"], world["admin"], title="Uno")
    h = login("admin@test.local")
    assert client.get("/api/tickets", params={"q": "000001"}, headers=h).json()["total"] == 1
    assert client.get("/api/tickets", params={"q": "tck-000001"}, headers=h).json()["total"] == 1
    assert client.get("/api/tickets", params={"q": "x" * 101}, headers=h).status_code == 422


def test_date_filters_edges(client, world, make_ticket, login):
    h = login("admin@test.local")
    today = NOW.date()
    make_ticket(world["client"], world["admin"], title="Hoy", created_at=datetime.combine(today, datetime.min.time(), UTC))
    make_ticket(world["client"], world["admin"], title="Ayer 23:59",
                created_at=datetime.combine(today, datetime.min.time(), UTC) - timedelta(minutes=1))

    def total(**p):
        return client.get("/api/tickets", params=p, headers=h).json()["total"]

    assert total(created_from=today.isoformat(), created_to=today.isoformat()) == 1  # límites inclusivos por día UTC
    assert total(created_to=(today - timedelta(days=1)).isoformat()) == 1
    assert total(created_from=today.isoformat(), created_to=(today - timedelta(days=3)).isoformat()) == 0  # rango invertido
    assert client.get("/api/tickets", params={"created_from": "16/10/2026"}, headers=h).status_code == 422


def test_due_date_without_timezone_is_utc_and_overdue_logic(client, world, login):
    h = login("admin@test.local")
    base = {"client_id": world["client"], "category_id": 1}
    past = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="seconds")
    r = client.post("/api/tickets", headers=h, json={**base, "title": "Vencido", "due_at": past})
    assert r.json()["due_at"].endswith("Z") and r.json()["is_overdue"] is True
    tid = r.json()["id"]
    client.post(f"/api/tickets/{tid}/status", headers=h, json={"status": "cancelado", "reason": "x"})
    assert client.get(f"/api/tickets/{tid}", headers=h).json()["is_overdue"] is False  # cerrado/cancelado no vence
    assert client.get("/api/tickets", params={"overdue": "true"}, headers=h).json()["total"] == 0


def test_inactive_assignee_cannot_be_assigned_but_history_is_kept(client, world, login, make_user):
    h = login("admin@test.local")
    r = client.post("/api/tickets", headers=h, json={"title": "Ticket uno", "client_id": world["client"], "category_id": 1,
                                                     "assignee_id": world["agent1"]})
    tid = r.json()["id"]
    client.delete(f"/api/users/{world['agent1']}", headers=h)
    assert client.get(f"/api/tickets/{tid}", headers=h).json()["assignee_name"] == "Agente Uno"
    assert world["agent1"] not in {a["id"] for a in client.get("/api/lookups/assignees", headers=h).json()}
    assert client.post(f"/api/tickets/{tid}/assign", headers=h, json={"assignee_id": world["agent1"]}).status_code == 400


def test_deleted_ticket_is_invisible_everywhere(client, world, make_ticket, login):
    h = login("admin@test.local")
    tid = make_ticket(world["client"], world["admin"], status="en_proceso", assignee_id=world["agent1"])
    client.delete(f"/api/tickets/{tid}", headers=h)
    for path in (f"/api/tickets/{tid}", f"/api/tickets/{tid}/events", f"/api/tickets/{tid}/comments"):
        assert client.get(path, headers=h).status_code == 404
    for method, path, body in (("patch", f"/api/tickets/{tid}", {"title": "abc"}),
                               ("post", f"/api/tickets/{tid}/status", {"status": "pendiente", "reason": "x"}),
                               ("post", f"/api/tickets/{tid}/assign", {"assignee_id": None})):
        assert getattr(client, method)(path, json=body, headers=h).status_code == 404
    assert client.get("/api/dashboard", headers=h).json()["total"] == 0
    assert len(client.get("/api/tickets/export", headers=h).text.strip().splitlines()) == 1


def test_role_with_only_inactive_users_can_be_deactivated_and_reactivated(client, world, login, make_role, make_user):
    h = login("admin@test.local")
    rid = make_role("Temporal", tickets=1)
    make_user("temp@test.local", rid, active=False)
    assert client.delete(f"/api/roles/{rid}", headers=h).status_code == 200
    assert rid not in {r["id"] for r in client.get("/api/lookups/roles", headers=h).json()}
    r = client.post("/api/users", headers=h, json={"email": "n@test.local", "full_name": "N",
                                                   "password": "Password#12345", "role_id": rid})
    assert r.status_code == 400  # rol inactivo no asignable
    assert client.post(f"/api/roles/{rid}/activate", headers=h).status_code == 200


def test_client_rename_conflicts_are_case_insensitive(client, world, login, make_client):
    h = login("admin@test.local")
    other = make_client("Beta")
    assert client.patch(f"/api/clients/{other}", json={"name": "CLIENTE a"}, headers=h).status_code == 409
    assert client.patch(f"/api/clients/{other}", json={"name": "Beta"}, headers=h).status_code == 200  # mismo nombre


@pytest.mark.parametrize("params,status", [
    ({}, 200), ({"date_from": "2026-01-01"}, 200), ({"date_to": "2026-01-01"}, 200),
    ({"date_from": (date.today() - timedelta(days=365)).isoformat()}, 200),
    ({"date_from": (date.today() - timedelta(days=366)).isoformat()}, 400),
    ({"priority": "urgente"}, 422), ({"client_id": 999}, 200), ({"assignee_id": 999, "category_id": 3}, 200),
])
def test_dashboard_parameter_matrix(client, world, login, params, status):
    r = client.get("/api/dashboard", params=params, headers=login("admin@test.local"))
    assert r.status_code == status


def test_dashboard_series_spans_exactly_the_period(client, world, login):
    h = login("admin@test.local")
    d = client.get("/api/dashboard", params={"date_from": "2026-02-27", "date_to": "2026-03-02"}, headers=h).json()
    assert [p["date"] for p in d["series"]] == ["2026-02-27", "2026-02-28", "2026-03-01", "2026-03-02"]


def test_dashboard_equals_listing_counts(client, world, make_ticket, login):
    """Coherencia cruzada: cada indicador coincide con el total del listado con el filtro equivalente."""
    h = login("admin@test.local")
    for i, st in enumerate(["sin_inicio", "iniciado", "en_proceso", "pendiente", "cerrado", "cancelado", "rechazado"] * 3):
        assignee = None if st in ("sin_inicio", "cancelado", "rechazado") else world["agent1"]
        make_ticket(world["client"], world["admin"], status=st, assignee_id=assignee,
                    due_at=NOW - timedelta(days=1) if i % 2 else None)
    d = client.get("/api/dashboard", headers=h).json()

    def total(**p):
        return client.get("/api/tickets", params=p, headers=h).json()["total"]

    assert d["total"] == total()
    assert d["open"] == total(status=["sin_inicio", "iniciado", "en_proceso", "pendiente"])
    assert d["closed"] == total(status="cerrado") and d["pending"] == total(status="pendiente")
    assert d["overdue"] == total(overdue="true")
    assert {i["key"]: i["count"] for i in d["by_status"]}["cancelado"] == total(status="cancelado")


def test_comment_limits(client, world, login, make_ticket):
    h = login("admin@test.local")
    tid = make_ticket(world["client"], world["admin"])
    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "c" * 5000}, headers=h).status_code == 201
    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "c" * 5001}, headers=h).status_code == 422
    assert client.post(f"/api/tickets/{tid}/comments", json={}, headers=h).status_code == 422


def test_reopen_then_close_counts_two_closure_events_and_keeps_latest_resolution(client, world, login, make_ticket):
    h = login("admin@test.local")
    tid = make_ticket(world["client"], world["admin"], status="en_proceso", assignee_id=world["agent1"])
    client.post(f"/api/tickets/{tid}/status", json={"status": "cerrado", "resolution": "Primera"}, headers=h)
    client.post(f"/api/tickets/{tid}/status", json={"status": "en_proceso", "reason": "Volvió"}, headers=h)
    t = client.post(f"/api/tickets/{tid}/status", json={"status": "cerrado", "resolution": "Segunda"}, headers=h).json()
    assert t["resolution"] == "Segunda"
    today = date.today().isoformat()
    d = client.get("/api/dashboard", params={"date_from": today, "date_to": today}, headers=h).json()
    assert d["series"][-1]["closed"] == 2


def test_permission_change_takes_effect_without_relogin(client, world, login):
    agent = login("agent1@test.local")
    admin = login("admin@test.local")
    assert client.get("/api/dashboard", headers=agent).status_code == 200
    client.patch(f"/api/roles/{world['agent_role']}", json={"permissions": {"dashboard": 0}}, headers=admin)
    assert client.get("/api/dashboard", headers=agent).status_code == 403
    assert client.get("/api/auth/me", headers=agent).json()["permissions"]["dashboard"] == 0


def test_health_and_meta(client, world, login):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/meta").status_code == 401
    meta = client.get("/api/meta", headers=login("agent1@test.local")).json()
    assert meta["transitions"]["cerrado"] == ["en_proceso"] and len(meta["categories"]) == 3


def test_validation_messages_are_spanish_and_name_the_field(client, world, login):
    h = login("admin@test.local")
    r = client.post("/api/tickets", headers=h, json={"title": "   ab  ", "client_id": world["client"], "category_id": 1})
    assert r.status_code == 422
    assert r.json()["error"]["details"] == [{"field": "title", "message": "Título: debe tener al menos 3 caracteres"}]
    r = client.get("/api/tickets/abc", headers=h)
    assert r.json()["error"]["details"][0]["message"] == "Ticket: debe ser un número entero"
    r = client.get("/api/tickets", headers=h, params={"q": "x" * 101})
    assert r.json()["error"]["details"][0]["message"] == "Búsqueda: admite como máximo 100 caracteres"
    r = client.post("/api/tickets", headers=h, json={"client_id": world["client"], "category_id": 1})
    assert r.json()["error"]["details"][0]["message"] == "Título: es obligatorio"
