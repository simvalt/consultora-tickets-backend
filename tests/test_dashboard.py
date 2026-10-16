"""Métricas con datos conocidos."""
from datetime import UTC, datetime, timedelta

import pytest

TODAY = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def day(n: int) -> datetime:
    return TODAY - timedelta(days=n)


@pytest.fixture
def known(world, make_client, make_ticket):
    c2 = make_client("Cliente B")
    a1, a2, adm, c1 = world["agent1"], world["agent2"], world["admin"], world["client"]
    mk = make_ticket
    mk(c1, adm, status="sin_inicio", priority="baja", created_at=day(1), due_at=TODAY - timedelta(days=3))   # vencido, sin responsable
    mk(c1, adm, status="iniciado", priority="media", assignee_id=a1, created_at=day(2))
    mk(c1, adm, status="en_proceso", priority="alta", assignee_id=a1, created_at=day(3), due_at=TODAY + timedelta(days=5))
    mk(c2, adm, status="pendiente", priority="alta", assignee_id=a2, created_at=day(4), due_at=TODAY - timedelta(hours=1))  # vencido
    mk(c2, adm, status="cerrado", priority="critica", assignee_id=a2, created_at=day(5), closed_at=day(5) + timedelta(hours=10),
       due_at=TODAY - timedelta(days=100))  # cerrado: no cuenta como vencido
    mk(c2, adm, status="cerrado", priority="media", assignee_id=a1, created_at=day(6), closed_at=day(6) + timedelta(hours=30))
    mk(c1, adm, status="cancelado", priority="baja", created_at=day(7))
    mk(c1, adm, status="rechazado", priority="baja", created_at=day(40))
    mk(c1, adm, status="en_proceso", priority="alta", assignee_id=a1, created_at=day(1), deleted=True)  # eliminado: ignorar
    return {"c2": c2}


def get(client, login, **params):
    r = client.get("/api/dashboard", params=params, headers=login("admin@test.local"))
    assert r.status_code == 200, r.text
    return r.json()


def test_general_indicators(client, known, login):
    d = get(client, login)
    assert (d["total"], d["open"], d["pending"], d["closed"], d["cancelled"], d["rejected"], d["overdue"]) == (8, 4, 1, 2, 1, 1, 2)
    by_status = {i["key"]: i["count"] for i in d["by_status"]}
    assert by_status == {"sin_inicio": 1, "iniciado": 1, "en_proceso": 1, "pendiente": 1, "cancelado": 1, "cerrado": 2, "rechazado": 1}
    by_priority = {i["key"]: i["count"] for i in d["by_priority"]}
    assert by_priority == {"baja": 3, "media": 2, "alta": 2, "critica": 1}
    assert d["avg_resolution_hours"] == 20.0  # (10 h + 30 h) / 2


def test_open_by_assignee_and_by_client(client, known, login, world):
    d = get(client, login)
    open_by = {i["label"]: i["count"] for i in d["open_by_assignee"]}
    assert open_by == {"Agente Uno": 2, "Sin responsable": 1, "Agente Dos": 1}
    by_client = {i["label"]: i["count"] for i in d["by_client"]}
    assert by_client == {"Cliente A": 5, "Cliente B": 3}


def test_period_applies_to_creation_date(client, known, login):
    d = get(client, login, date_from=day(10).date().isoformat(), date_to=day(0).date().isoformat())
    assert d["total"] == 7 and d["rejected"] == 0  # excluye el de hace 40 días
    d = get(client, login, date_from=day(5).date().isoformat(), date_to=day(5).date().isoformat())
    assert d["total"] == 1 and d["closed"] == 1


def test_common_filters(client, known, login, world):
    assert get(client, login, client_id=known["c2"])["total"] == 3
    assert get(client, login, assignee_id=world["agent1"])["total"] == 3
    assert get(client, login, priority="alta")["total"] == 2
    assert get(client, login, category_id=2)["total"] == 0
    combo = get(client, login, client_id=known["c2"], priority="critica")
    assert combo["total"] == 1 and combo["closed"] == 1 and combo["avg_resolution_hours"] == 10.0


def test_empty_state(client, world, login):
    d = get(client, login)
    assert d["total"] == 0 and d["avg_resolution_hours"] is None and d["overdue"] == 0
    assert d["by_client"] == [] and d["open_by_assignee"] == []
    assert len(d["series"]) == 30 and all(p["created"] == 0 and p["closed"] == 0 for p in d["series"])
    assert all(i["count"] == 0 for i in d["by_status"])


def test_series_uses_event_dates(client, known, login, make_ticket, world):
    start, end = day(8).date(), day(0).date()
    d = get(client, login, date_from=start.isoformat(), date_to=end.isoformat())
    s = {p["date"]: p for p in d["series"]}
    assert len(d["series"]) == 9 and d["series_from"] == start.isoformat() and d["series_to"] == end.isoformat()
    assert s[day(1).date().isoformat()]["created"] == 1  # el eliminado no cuenta
    # Un cierre ocurre 10 h después de crearse (mismo día 5); el otro 30 h después de crearse el día 6 (cae el día 5).
    assert s[day(5).date().isoformat()]["created"] == 1 and s[day(5).date().isoformat()]["closed"] == 2
    assert s[day(6).date().isoformat()]["created"] == 1 and s[day(6).date().isoformat()]["closed"] == 0
    assert sum(p["created"] for p in d["series"]) == 7 and sum(p["closed"] for p in d["series"]) == 2


def test_closed_series_counts_closure_event_date_not_creation(client, world, make_ticket, login):
    make_ticket(world["client"], world["admin"], status="cerrado", assignee_id=world["agent1"],
                created_at=day(20), closed_at=day(2))
    d = get(client, login, date_from=day(5).date().isoformat(), date_to=day(0).date().isoformat())
    assert d["total"] == 0  # creado fuera del periodo (indicadores usan la fecha de creación)
    assert sum(p["closed"] for p in d["series"]) == 1  # pero el cierre ocurrió en el periodo
    assert {p["date"]: p["closed"] for p in d["series"]}[day(2).date().isoformat()] == 1


def test_range_validation(client, world, login):
    h = login("admin@test.local")
    r = client.get("/api/dashboard", params={"date_from": "2026-02-01", "date_to": "2026-01-01"}, headers=h)
    assert r.status_code == 400
    r = client.get("/api/dashboard", params={"date_from": "2020-01-01", "date_to": "2026-01-01"}, headers=h)
    assert r.status_code == 400


def test_dashboard_export(client, known, login):
    r = client.get("/api/dashboard/export", headers=login("admin@test.local"))
    assert r.status_code == 200
    assert "Total de tickets,8" in r.text and "Vencidos,2" in r.text
