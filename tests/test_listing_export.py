"""Filtros, orden, paginación y exportación CSV (debe coincidir con el listado)."""
import csv
import io
from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def data(world, make_client, make_ticket):
    """45 tickets distribuidos entre estatus, prioridades, clientes y responsables."""
    c2 = make_client("Cliente B")
    statuses = ["sin_inicio", "iniciado", "en_proceso", "pendiente", "cerrado", "cancelado", "rechazado"]
    prios = ["baja", "media", "alta", "critica"]
    ids = []
    for i in range(45):
        status = statuses[i % 7]
        assignee = None if status in ("sin_inicio", "cancelado", "rechazado") else (world["agent1"] if i % 2 else world["agent2"])
        ids.append(make_ticket(
            world["client"] if i % 3 else c2, world["admin"], title=f"Ticket número {i:02d}",
            description="Contiene la palabra especial" if i % 10 == 0 else "Descripción común",
            status=status, priority=prios[i % 4], assignee_id=assignee, category_id=1 + i % 3,
            created_at=NOW - timedelta(days=i), due_at=(NOW - timedelta(days=1)) if i % 5 == 0 else None,
        ))
    return {"c2": c2, "ids": ids}


def parse_csv(text: str) -> list[list[str]]:
    assert text.startswith("﻿"), "falta el BOM UTF-8"
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def all_pages(client, headers, path, params):
    items, page = [], 1
    while True:
        r = client.get(path, params={**params, "page": page, "page_size": 20}, headers=headers)
        body = r.json()
        items += body["items"]
        if page >= body["pages"]:
            return items, body
        page += 1


FILTERS = [
    {},
    {"status": ["pendiente", "cerrado"]},
    {"priority": ["alta", "critica"], "sort": "priority", "order": "desc"},
    {"q": "palabra especial"},
    {"q": "TCK-000007"},
    {"overdue": "true"},
    {"unassigned": "true", "sort": "title", "order": "asc"},
    {"assignee_id": "AGENT1", "sort": "folio", "order": "asc"},
    {"client_id": "C2", "status": ["iniciado"]},
    {"category_id": 2, "sort": "created_at", "order": "asc"},
    {"created_from": (NOW - timedelta(days=20)).date().isoformat(), "created_to": (NOW - timedelta(days=5)).date().isoformat()},
]


@pytest.mark.parametrize("params", FILTERS, ids=[str(i) for i in range(len(FILTERS))])
def test_csv_matches_listing_across_pages(client, world, data, login, params):
    h = login("admin@test.local")
    params = {k: (world["agent1"] if v == "AGENT1" else data["c2"] if v == "C2" else v) for k, v in params.items()}
    items, body = all_pages(client, h, "/api/tickets", params)
    assert len(items) == body["total"]
    r = client.get("/api/tickets/export", params=params, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = parse_csv(r.text)
    header, lines = rows[0], rows[1:]
    assert header[0] == "Folio" and "Título" in header and "Responsable" in header
    # Mismos registros y mismo orden que el listado completo (todas las páginas).
    assert [row[0] for row in lines] == [i["folio"] for i in items]


def test_export_includes_records_beyond_first_page(client, data, login):
    h = login("admin@test.local")
    page1 = client.get("/api/tickets", params={"page_size": 20}, headers=h).json()
    assert page1["total"] == 45 and len(page1["items"]) == 20
    rows = parse_csv(client.get("/api/tickets/export", headers=h).text)
    assert len(rows) - 1 == 45


def test_pagination_metadata_and_consistency(client, data, login):
    h = login("admin@test.local")
    body = client.get("/api/tickets", params={"page": 3, "page_size": 20, "sort": "folio", "order": "asc"}, headers=h).json()
    assert body["total"] == 45 and body["pages"] == 3 and len(body["items"]) == 5 and body["page"] == 3
    assert body["items"][0]["folio"] == "TCK-000041"
    assert client.get("/api/tickets", params={"page_size": 101}, headers=h).status_code == 422
    assert client.get("/api/tickets", params={"page": 0}, headers=h).status_code == 422
    empty = client.get("/api/tickets", params={"page": 9}, headers=h).json()
    assert empty["items"] == [] and empty["total"] == 45


def test_filters_individually(client, world, data, login):
    h = login("admin@test.local")

    def total(**p):
        return client.get("/api/tickets", params=p, headers=h).json()["total"]

    assert total(status="sin_inicio") == 7
    assert total(status=["sin_inicio", "cerrado"]) == 13
    assert total(unassigned="true") == 7 + 6 + 6  # sin_inicio + cancelado + rechazado
    assert total(unassigned="true", status="iniciado") == 0
    assert total(overdue="true") == len([i for i in range(45) if i % 5 == 0 and i % 7 not in (4, 5, 6)])
    assert total(q="especial") == 5
    assert total(q="ticket número 07") == 1
    assert total(q="%") == 0 and total(q="_") == 0  # los comodines del usuario se escapan
    assert total(client_id=data["c2"]) == 15
    assert total(client_id=world["client"]) == 30
    assert total(priority="critica") == 11
    assert total(sort="status") == 45
    assert client.get("/api/tickets", params={"status": "inventado"}, headers=h).status_code == 422
    assert client.get("/api/tickets", params={"sort": "password"}, headers=h).status_code == 422


def test_sorting(client, data, login):
    h = login("admin@test.local")
    asc = client.get("/api/tickets", params={"sort": "created_at", "order": "asc", "page_size": 100}, headers=h).json()["items"]
    dates = [i["created_at"] for i in asc]
    assert dates == sorted(dates)
    prio = client.get("/api/tickets", params={"sort": "priority", "order": "desc", "page_size": 100}, headers=h).json()["items"]
    assert prio[0]["priority"] == "critica" and prio[-1]["priority"] == "baja"


def test_export_requires_read_permission_and_applies_filters(client, world, data, make_role, make_user, login):
    make_user("nada@test.local", make_role("Nada"))
    assert client.get("/api/tickets/export", headers=login("nada@test.local")).status_code == 403
    assert client.get("/api/tickets/export").status_code == 401


def test_csv_escaping_and_formula_protection(client, world, make_ticket, login):
    h = login("admin@test.local")
    make_ticket(world["client"], world["admin"], title='=HYPERLINK("http://x","click")',
                description='Línea 1, con coma\nLínea 2 "entre comillas"')
    make_ticket(world["client"], world["admin"], title="+SUMA(1;2)", description="@cmd")
    make_ticket(world["client"], world["admin"], title="-2+3", description="\tTabulado")
    make_ticket(world["client"], world["admin"], title="Acentos áéíóú ñ", description="normal")
    r = client.get("/api/tickets/export", params={"sort": "folio", "order": "asc"}, headers=h)
    rows = parse_csv(r.text)
    titles = {row[1]: row for row in rows[1:]}
    assert "'=HYPERLINK(\"http://x\",\"click\")" in titles
    assert "'+SUMA(1;2)" in titles and "'-2+3" in titles
    first = titles["'=HYPERLINK(\"http://x\",\"click\")"]
    assert first[2] == 'Línea 1, con coma\nLínea 2 "entre comillas"'  # ida y vuelta exacta
    assert titles["'+SUMA(1;2)"][2] == "'@cmd" and titles["'-2+3"][2] == "'\tTabulado"
    assert "Acentos áéíóú ñ" in titles
    assert "áéíóú".encode() in r.content


def test_users_export_excludes_secrets_and_follows_filters(client, world, login):
    h = login("admin@test.local")
    r = client.get("/api/users/export", params={"q": "agent", "sort": "email", "order": "desc"}, headers=h)
    rows = parse_csv(r.text)
    assert rows[0] == ["Nombre", "Correo", "Rol", "Activo", "Creado", "Último acceso"]
    assert [row[1] for row in rows[1:]] == ["agent2@test.local", "agent1@test.local"]
    assert "hash" not in r.text.lower() and "argon" not in r.text.lower()
    listing = client.get("/api/users", params={"q": "agent", "sort": "email", "order": "desc"}, headers=h).json()
    assert [u["email"] for u in listing["items"]] == [row[1] for row in rows[1:]]
    assert all("password" not in k for k in listing["items"][0])


def test_roles_and_clients_export(client, world, login):
    h = login("admin@test.local")
    roles = parse_csv(client.get("/api/roles/export", headers=h).text)
    assert roles[0][:4] == ["Rol", "Descripción", "Activo", "Usuarios"] and roles[0][4:] == [
        "Tickets", "Usuarios", "Roles y permisos", "Clientes", "Dashboard"]
    admin_row = next(r for r in roles if r[0] == "Administrador")
    assert admin_row[4:] == ["Eliminar"] * 5
    clients = parse_csv(client.get("/api/clients/export", params={"q": "cliente"}, headers=h).text)
    assert clients[0][0] == "Nombre" and len(clients) == 2


def test_clients_listing_filters(client, world, make_client, login):
    h = login("admin@test.local")
    make_client("Dormido", active=False)
    assert client.get("/api/clients", params={"is_active": "false"}, headers=h).json()["total"] == 1
    assert client.get("/api/clients", params={"q": "dorm"}, headers=h).json()["total"] == 1
    assert client.get("/api/clients", headers=h).json()["total"] == 2
