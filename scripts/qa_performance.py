"""Prueba de carga/rendimiento reproducible (QA).

Crea la base `tickets_perf`, aplica las migraciones del repo database, carga un volumen grande de datos
sintéticos y mide las operaciones principales a través de la API (TestClient, sin red).

Uso (con PostgreSQL local en marcha):
    python scripts/qa_performance.py [--tickets 200000]

Variables: PERF_DATABASE_URL (por defecto postgresql://tickets:change_me_local@127.0.0.1:5432/tickets_perf),
DATABASE_REPO_PATH (por defecto ../consultora-tickets-database).
"""
import argparse
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PERF_URL = os.environ.get("PERF_DATABASE_URL", "postgresql://tickets:change_me_local@127.0.0.1:5432/tickets_perf")
DB_REPO = Path(os.environ.get("DATABASE_REPO_PATH", ROOT.parent / "consultora-tickets-database"))


def prepare(n_tickets: int) -> None:
    url = make_url(PERF_URL)
    admin = str(url.set(database="postgres").render_as_string(hide_password=False))
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{url.database}"')
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=DB_REPO, check=True,
                   env={**os.environ, "DATABASE_URL": PERF_URL}, capture_output=True)
    from app.security import hash_password

    t0 = time.perf_counter()
    with psycopg.connect(PERF_URL, autocommit=True) as conn:
        conn.execute("INSERT INTO ticket_categories VALUES (1,'incidente','Incidente'),(2,'consulta','Consulta')")
        conn.execute("INSERT INTO roles (name) VALUES ('Administrador')")
        conn.execute("INSERT INTO role_permissions SELECT 1, m, 4 FROM unnest(ARRAY['tickets','users','roles',"
                     "'clients','dashboard']) m")
        conn.execute("INSERT INTO users (email, full_name, password_hash, role_id) VALUES "
                     "('perf@test.local','Perf',%s,1)", (hash_password("Password#12345"),))
        conn.execute("INSERT INTO users (email, full_name, password_hash, role_id) "
                     "SELECT 'agente'||g||'@test.local', 'Agente '||g, 'x', 1 FROM generate_series(1,50) g")
        conn.execute("INSERT INTO clients (name) SELECT 'Cliente '||g FROM generate_series(1,300) g")
        conn.execute(  # n_tickets es un int validado por argparse: no hay entrada de usuario en el SQL
            f"""
            INSERT INTO tickets (title, description, client_id, category_id, priority, status, created_by,
                                 assignee_id, created_at, updated_at, started_at, closed_at, due_at)
            SELECT 'Incidencia '||g||' en '||(ARRAY['VPN','correo','ERP','red','impresora'])[1+g%5],
                   'Descripción del problema número '||g,
                   1 + g % 300, 1 + g % 2,
                   (ARRAY['baja','media','alta','critica'])[1+g%4],
                   s.status, 1,
                   CASE WHEN s.status IN ('sin_inicio','cancelado','rechazado') THEN NULL ELSE 2 + g % 50 END,
                   c.created, c.created, c.created + interval '1 hour',
                   CASE WHEN s.status = 'cerrado' THEN c.created + (g % 72) * interval '1 hour' END,
                   CASE WHEN g % 3 = 0 THEN c.created + interval '3 days' END
            FROM generate_series(1, {int(n_tickets)}) g
            CROSS JOIN LATERAL (SELECT (ARRAY['sin_inicio','iniciado','en_proceso','pendiente','cerrado',
                                              'cancelado','rechazado'])[1+g%7] AS status) s
            CROSS JOIN LATERAL (SELECT now() - (g % 365) * interval '1 day' - (g % 24) * interval '1 hour'
                                AS created) c
            """
        )
        conn.execute("INSERT INTO ticket_events (ticket_id, event_type, to_status, actor_id, created_at) "
                     "SELECT id, 'created', 'sin_inicio', 1, created_at FROM tickets")
        conn.execute("INSERT INTO ticket_events (ticket_id, event_type, from_status, to_status, actor_id, created_at) "
                     "SELECT id, 'status_change', 'en_proceso', 'cerrado', 1, closed_at FROM tickets "
                     "WHERE status = 'cerrado'")
        conn.execute("INSERT INTO ticket_comments (ticket_id, author_id, body) "
                     "SELECT id, 1, 'Comentario de seguimiento' FROM tickets WHERE id % 2 = 0")
        conn.execute("ANALYZE")
    print(f"Datos cargados: {n_tickets:,} tickets en {time.perf_counter() - t0:.1f} s")


def bench(client, headers, name, method, path, params=None, runs=7):
    times = []
    status = None
    size = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        r = client.request(method, path, params=params, headers=headers)
        times.append((time.perf_counter() - t0) * 1000)
        status, size = r.status_code, len(r.content)
    times.sort()
    p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
    print(f"| {name} | {status} | {statistics.median(times):.0f} | {p95:.0f} | {size / 1024:.0f} KB |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", type=int, default=200_000)
    parser.add_argument("--skip-load", action="store_true")
    args = parser.parse_args()

    os.environ["DATABASE_URL"] = PERF_URL
    os.environ.setdefault("JWT_SECRET", "perf-secret-perf-secret-perf-secret-123456")
    os.environ["COOKIE_SECURE"] = "false"
    sys.path.insert(0, str(ROOT))
    if not args.skip_load:
        prepare(args.tickets)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        tok = c.post("/api/auth/login", json={"email": "perf@test.local", "password": "Password#12345"}).json()
        h = {"Authorization": f"Bearer {tok['access_token']}"}
        print("\n| Operación | HTTP | Mediana ms | p95 ms | Tamaño |\n|---|---|---|---|---|")
        bench(c, h, "Listado tickets (pág. 1, 20)", "GET", "/api/tickets")
        bench(c, h, "Listado página 5 000", "GET", "/api/tickets", {"page": 5000})
        bench(c, h, "Búsqueda texto «impresora»", "GET", "/api/tickets", {"q": "impresora"})
        bench(c, h, "Búsqueda folio parcial", "GET", "/api/tickets", {"q": "TCK-0001234"})
        bench(c, h, "Filtro estatus+prioridad+cliente", "GET", "/api/tickets",
              {"status": ["pendiente", "en_proceso"], "priority": "alta", "client_id": 17})
        bench(c, h, "Vencidos ordenados por vencimiento", "GET", "/api/tickets",
              {"overdue": "true", "sort": "due_at", "order": "asc"})
        bench(c, h, "Detalle de ticket", "GET", "/api/tickets/12345")
        bench(c, h, "Historial de ticket", "GET", "/api/tickets/12345/events")
        bench(c, h, "Dashboard 30 días", "GET", "/api/dashboard")
        bench(c, h, "Dashboard 1 año + cliente", "GET", "/api/dashboard",
              {"date_from": "2025-10-17", "client_id": 17})
        bench(c, h, "Dashboard histórico completo", "GET", "/api/dashboard",
              {"date_from": "2025-10-17", "date_to": "2026-10-16"})
        bench(c, h, "Export CSV filtrado (alta)", "GET", "/api/tickets/export", {"priority": "alta"}, runs=3)
        bench(c, h, "Export CSV completo", "GET", "/api/tickets/export", runs=2)
        bench(c, h, "/auth/me (permisos)", "GET", "/api/auth/me", runs=30)

    with psycopg.connect(PERF_URL) as conn:
        print("\nPlanes de ejecución (resumen):")
        for label, sql in [
            ("búsqueda ILIKE", "SELECT count(*) FROM tickets t WHERE t.deleted_at IS NULL AND "
                              "(t.folio ILIKE '%impresora%' OR t.title ILIKE '%impresora%' "
                              "OR t.description ILIKE '%impresora%')"),
            ("vencidos", "SELECT id FROM tickets t WHERE deleted_at IS NULL AND due_at < now() "
                         "AND status IN ('sin_inicio','iniciado','en_proceso','pendiente') ORDER BY due_at LIMIT 20"),
            ("serie de cierres", "SELECT (created_at AT TIME ZONE 'UTC')::date d, count(*) FROM ticket_events "
                                 "WHERE event_type='status_change' AND to_status='cerrado' "
                                 "AND created_at >= now() - interval '30 days' GROUP BY d"),
        ]:
            plan = [r[0] for r in conn.execute("EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) " + sql)]
            used = [line.strip() for line in plan if "Scan" in line][:3]
            total = next((line for line in plan if "Execution Time" in line), "")
            print(f"- {label}: {'; '.join(used)} — {total.strip()}")


if __name__ == "__main__":
    main()
