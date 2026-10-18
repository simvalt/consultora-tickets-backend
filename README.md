# consultora-tickets-backend

API (FastAPI + PostgreSQL) del sistema interno de tickets de la consultora. **Este repositorio contiene además el `docker-compose.yml` que levanta los tres proyectos**, así que esta es la **guía principal** de la solución.

| Repositorio | Contenido |
|---|---|
| `consultora-tickets-database` | Migraciones Alembic (única fuente del esquema), seeds, respaldo/restauración, modelo de datos |
| `consultora-tickets-backend` (este) | API FastAPI, reglas de negocio, pruebas, `docker-compose.yml` de orquestación |
| `consultora-tickets-frontend` | SPA Angular + nginx (sirve la app y hace proxy de `/api`) |

Deben estar **como carpetas hermanas**:

```
<carpeta>/
├── consultora-tickets-database/
├── consultora-tickets-backend/      ← ejecuta aquí docker compose
└── consultora-tickets-frontend/
```

Otras ubicaciones: `DATABASE_REPO_PATH` y `FRONTEND_REPO_PATH` en `.env`.

## Guía rápida: entorno completo desde cero

Requisitos: Docker (Compose v2) y Git. (Para desarrollo sin Docker: Python 3.12 y Node 22.)

```bash
cd consultora-tickets-backend
cp .env.example .env                       # valores locales de ejemplo; revisa JWT_SECRET y contraseñas
docker compose --profile demo up -d --build
```

El orden de arranque lo gestiona Compose: `db` (healthy) → `migrate` (aplica Alembic del repo *database* y crea el administrador inicial; termina) → `seed-demo` (perfil `demo`, opcional) → `backend` (healthy) → `frontend`.

| Servicio | URL local |
|---|---|
| Aplicación | http://localhost:4200 |
| API / OpenAPI (Swagger) | http://localhost:8000/api/docs (también en `http://localhost:4200/api/docs`) |
| PostgreSQL | `127.0.0.1:5432` |

**Credenciales LOCALES de demostración** (definidas en `.env.example`; no usar en producción):

| Usuario | Rol | Contraseña |
|---|---|---|
| `admin@consultora.local` | Administrador (acceso total) | `Admin#Local1234` |
| `supervisor@consultora.local` | Supervisor (tickets 4, clientes 3, usuarios 1, dashboard 1) | `Demo#Local1234` |
| `agente1@`, `agente2@`, `agente3@consultora.local` | Agente (tickets 3, clientes 1, dashboard 1) | `Demo#Local1234` |
| `capturista@consultora.local` | Capturista (tickets 2, clientes 1) | `Demo#Local1234` |
| `consulta@consultora.local` | Consulta (solo lectura) | `Demo#Local1234` |
| `inactivo@consultora.local` | Usuario desactivado (no puede entrar) | `Demo#Local1234` |

Sin el perfil `demo` (`docker compose up -d --build`) sólo se crea el administrador inicial (`ADMIN_EMAIL`/`ADMIN_PASSWORD`) y las 6 categorías. Los datos demo **nunca** se cargan automáticamente y el seed se niega a correr con `APP_ENV=production`.

Detener: `docker compose down` (conserva datos) o `docker compose down -v` (borra la base).

### Desarrollo sin Docker para la app

```bash
# 1) Base de datos: docker compose up -d db migrate   (o la del repo database)
# 2) Backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                # DATABASE_URL apunta a 127.0.0.1:5432
uvicorn app.main:app --reload --port 8000
# 3) Frontend (otra terminal, en consultora-tickets-frontend)
npm ci && npm start                                 # http://localhost:4200, proxy /api → :8000
```

## Variables de entorno

Ver `.env.example` (comentado). Las del backend:

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Conexión PostgreSQL (`postgresql://user:pass@host:5432/db`) |
| `JWT_SECRET` | Secreto de firma (**≥ 32 caracteres**, obligatorio, nunca en Git) |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | Vigencias (15 min / 7 días) |
| `COOKIE_SECURE` | `true` bajo HTTPS (producción) · `false` en `http://localhost` |
| `COOKIE_SAMESITE` | `lax` (por defecto) |
| `CORS_ORIGINS` | Orígenes permitidos separados por comas; vacío = sin CORS (mismo origen) |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_MAX_ATTEMPTS_IP` / `LOGIN_MAX_ATTEMPTS_EMAIL` / `LOGIN_WINDOW_SECONDS` | Intentos fallidos permitidos por IP+correo (5), por IP (20) y por correo (10) en la ventana (300 s) |
| `DOCS_ENABLED` | Publica/oculta `/api/docs` y `/api/openapi.json`. Vacío = activados salvo con `APP_ENV=production` |

Con `APP_ENV=production` el backend **no arranca** si `JWT_SECRET` es el de ejemplo o `COOKIE_SECURE=false`.
| `FORWARDED_ALLOW_IPS` | Proxies de confianza para `X-Forwarded-*` |
| `CLIENT_IP_HEADER` | Cabecera con la IP real puesta por el proxy (p. ej. `CF-Connecting-IP` detrás de Cloudflare Tunnel). Vacío = IP de la conexión |
| `APP_ENV` | `local` · `demo` (publicado con datos demo: mismas protecciones que producción) · `production` |

## Pruebas, lint y compilación

Las pruebas usan un **PostgreSQL real**; el esquema se crea ejecutando las migraciones Alembic del repo *database* (el backend no tiene copia del esquema). Con el contenedor `db` arriba:

```bash
pip install -r requirements-dev.txt
pytest                      # 312 pruebas (usa la base tickets_test; se crea sola)
pip-audit -r requirements.txt       # vulnerabilidades conocidas en dependencias
python scripts/qa_performance.py   # prueba de carga: 200 000 tickets en la base tickets_perf
ruff check .                # lint
docker build -t tickets-backend .   # compilación de la imagen
```

Variables: `TEST_DATABASE_URL` (por defecto `postgresql://tickets:change_me_local@127.0.0.1:5432/tickets_test`) y `DATABASE_REPO_PATH` (por defecto `../consultora-tickets-database`). Cubren: login correcto/incorrecto/inactivo y rotación de refresh; escalera de permisos por módulo y rechazo de operaciones no autorizadas; anti-escalada de privilegios; protección del último administrador; las 49 combinaciones de transiciones; motivo/resolución obligatorios; historial; filtros/orden/paginación; CSV idéntico al listado (más de una página) y protección contra fórmulas; métricas con datos conocidos. Además, `test_qa_security.py`, `test_qa_edge.py` y `test_qa_concurrency.py` cubren tokens manipulados, límites de intentos, asignación masiva, inyección, casos límite y carreras.

**Auditoría de seguridad, optimizaciones medidas y resultados de QA: [docs/AUDITORIA_QA.md](docs/AUDITORIA_QA.md).**

## Arquitectura

```
app/
  routers/        HTTP: rutas, dependencias de permisos, esquemas de entrada/salida
  services/       Reglas de negocio y transacciones (workflow, anti-escalada, último admin)
  repositories/   SQL parametrizado (SQLAlchemy Core `text()` con binds; sin ORM)
  workflow.py     Estatus y transiciones (código)       permissions.py  Módulos y niveles
  deps.py         Autenticación + require(módulo, nivel)  csvutil.py     CSV seguro
  errors.py       Errores uniformes                      query.py       Paginación/orden/exportación
```

Decisiones:

- **Una transacción por solicitud** (`get_db`): cambiar estatus/responsable y registrar el evento de historial se confirman juntos o se revierten juntos (hay una prueba de atomicidad).
- **SQL explícito en repositorios** en lugar de ORM: el esquema vive en otro repositorio, así que no hay modelos que sincronizar ni `create_all`. Todas las consultas usan parámetros enlazados; los nombres de columna de orden pasan por una lista blanca.
- **Permisos recalculados en cada solicitud** desde la BD: cambiar un rol o desactivar un usuario surte efecto de inmediato (el JWT sólo contiene el id).
- **Listado y exportación comparten el mismo constructor de filtros/orden** (`TicketQuery`, `UserQuery`, …), por lo que el CSV coincide con el listado completo, no sólo con la página visible. La exportación recorre los resultados con cursor de servidor (streaming).

## Autenticación y sesiones

- Login con correo/contraseña (argon2id). Mismo error para correo inexistente y contraseña incorrecta; usuarios o roles inactivos → `403 user_inactive`.
- **Access token** JWT (15 min) que el frontend guarda sólo en memoria. **Refresh token** opaco, aleatorio, en cookie `HttpOnly; Secure; SameSite=Lax; Path=/api/auth`; en BD sólo se guarda su SHA-256. Se **rota en cada renovación** (el anterior se revoca) y `POST /api/auth/logout` lo revoca. Cambiar contraseña/rol o desactivar a un usuario revoca sus refresh tokens; tras un cambio o restablecimiento de contraseña también se rechazan los access tokens emitidos antes (`users.password_changed_at`).
- **Cambio de la propia contraseña**: `POST /api/auth/change-password` exige la contraseña actual y devuelve una sesión nueva (cierra las demás). Un administrador puede restablecer la de otros por `PATCH /api/users/{id}`, nunca la suya por esa vía.
- Al expirar la sesión (o fallar la renovación) el frontend regresa al login.
- Límite de intentos fallidos en memoria con tres ventanas: IP+correo, IP (contra *credential stuffing*) y correo (contra ataques distribuidos). nginx sobrescribe `X-Forwarded-For`, así que no se puede falsear la IP.
- Entradas estrictas: campos desconocidos → 422; texto con NUL → 422; fechas sin zona → UTC. Todas las respuestas de `/api` llevan `Cache-Control: no-store`.
- Se registran (sin contraseñas ni tokens) los logins fallidos, los bloqueos y los cambios de contraseña.

## Permisos

Módulos: `tickets`, `users`, `roles`, `clients`, `dashboard`. Niveles **acumulativos** por rol y módulo: 0 Nada · 1 Leer · 2 Insertar (leer+crear) · 3 Editar (+modificar) · 4 Eliminar (+eliminar/desactivar).

| Operación | Nivel mínimo |
|---|---|
| Listar/ver/exportar CSV de un módulo | Leer |
| Crear (ticket, usuario, rol, cliente) | Insertar |
| Editar; **cambiar estatus, asignar, comentar, reabrir tickets**; reactivar | Editar |
| Eliminar ticket (lógico) / desactivar usuario, rol o cliente | Eliminar |
| Crear un ticket **con responsable** | Editar |

**Permisos propios por usuario (override).** Además del rol, un usuario puede tener niveles propios por módulo (tabla `user_permissions`). El nivel **efectivo** de cada módulo es el del usuario si lo definió (puede subir o bajar respecto al rol) y, si no, el del rol. Se aplica de inmediato en cada solicitud. `PATCH /api/users/{id}` con `permission_overrides` **reemplaza** el conjunto de overrides (`{}` los quita todos; omitir el campo no los toca). Las matrices (de usuario y de rol) se pueden enviar como JSON legible, con nombres de nivel y alias de módulo:

```json
{ "Ticket": "crear", "Analítica": "ver", "Usuario": "editar", "Permisos": "leer" }
```

Niveles: `nada`, `leer`/`ver`, `insertar`/`crear`, `editar`, `eliminar` (o 0-4). Módulos: `tickets`/`ticket`, `users`/`usuarios`, `roles`/`permisos`, `clients`/`clientes`, `dashboard`/`analitica`. La API devuelve `permissions` (efectivos) y `permission_overrides` (propios). En la interfaz, el diálogo de usuario y el de rol tienen una vista **Matriz** y una vista **JSON** sincronizadas.

Reglas adicionales (todas en el backend):

- **Administrador** = usuario activo con rol activo y nivel **efectivo** 4 en todos los módulos (los overrides cuentan).
- No puedes otorgar (por rol ni por override) permisos efectivos superiores a los tuyos, ni cambiar tus propios permisos. Ninguna operación (desactivar usuario, cambiar su rol, editar/desactivar un rol) puede dejar el sistema sin uno (`409 last_admin`; las operaciones se serializan con un advisory lock).
- **Anti-escalada**: sólo se pueden asignar/crear roles cuyos permisos no superen los propios en ningún módulo; no se puede modificar a un usuario con más privilegios, ni el propio rol, ni el rol asignado a uno mismo; no se puede desactivar uno mismo.
- Un rol con usuarios activos no puede desactivarse. Usuarios, roles y clientes se **desactivan** (no se borran); los tickets tienen **eliminación lógica** y conservan historial.
- Responsables asignables: usuarios activos cuyo rol tiene `tickets ≥ Editar`.

## Flujo de estatus

`sin_inicio → iniciado | cancelado | rechazado` · `iniciado → en_proceso | pendiente | cancelado` · `en_proceso → pendiente | cerrado | cancelado` · `pendiente → en_proceso | cancelado` · `cerrado → en_proceso` (reapertura) · `cancelado` y `rechazado` sin salidas.

- Motivo obligatorio para pendiente, cancelado, rechazado y reapertura; resumen de resolución obligatorio para cerrar.
- Iniciado y posteriores exigen responsable. `started_at` se fija la primera vez que pasa a *iniciado*; `closed_at` al cerrar y se limpia al reabrir (el evento histórico se conserva).
- Cada cambio de estatus/asignación genera un evento en `ticket_events` (anterior, nuevo, usuario, fecha, motivo) en la misma transacción.

## Dashboard: qué fecha usa cada métrica

- Indicadores (total, abiertos, pendientes, cerrados, cancelados, rechazados, vencidos), distribuciones, abiertos por responsable, tickets por cliente y tiempo promedio de resolución: el periodo se aplica a **`created_at`** del ticket. Vencidos = abiertos con `due_at < ahora`. Tiempo de resolución = promedio de `closed_at − created_at` de los tickets *actualmente* cerrados.
- Serie diaria **Creados**: `created_at`. Serie **Cerrados**: fecha del **evento de cierre** (un ticket reabierto y cerrado de nuevo cuenta en cada cierre). Días agrupados en UTC.
- Filtros: periodo, cliente, responsable, prioridad, categoría. Los tickets eliminados lógicamente no cuentan.

## CSV

`GET /api/<módulo>/export` acepta **los mismos filtros y orden** que el listado y devuelve **todos** los registros coincidentes (sin paginar). Requiere nivel Leer. UTF-8 con BOM, CRLF, comillas/comas/saltos de línea escapados, y valores que empiezan con `= + - @` o tab/CR se prefijan con `'` (anti inyección de fórmulas). Fechas en ISO-8601 UTC.

**Columnas elegidas por el usuario:** el parámetro repetible `columns=` indica qué columnas exportar y en qué orden, con las mismas claves que las tablas del frontend, por ejemplo `?columns=status&columns=folio&columns=title`. Sin `columns` se exportan todas en el orden por defecto. Una clave desconocida devuelve 422, así que nunca se puede pedir un campo que no esté en la lista blanca (hashes, tokens, etc.). Columnas disponibles (clave → encabezado):

- **tickets**: `folio` Folio, `title` Título, `description` Descripción, `client` Cliente, `category` Categoría, `priority` Prioridad, `status` Estatus, `created_by` Creado por, `assignee` Responsable, `created_at` Creado, `updated_at` Modificado, `started_at` Inicio de atención, `closed_at` Cierre, `due_at` Vencimiento, `resolution` Resolución
- **users**: `full_name`, `email`, `role`, `is_active`, `created_at`, `last_login_at` (sin hashes ni tokens)
- **roles**: `name`, `description`, `tickets`, `users`, `roles`, `clients`, `dashboard` (nivel por módulo), `user_count`, `is_active`
- **clients**: `name`, `contact_name`, `email`, `phone`, `is_active`, `created_at`
- **dashboard**: Sección, Elemento, Valor (formato largo fijo)

## API

OpenAPI interactivo en `/api/docs`. Errores uniformes: `{"error": {"code", "message", "details"}}` (sin trazas). Resumen: `/api/auth/{login,refresh,logout,me}`, `/api/{users,roles,clients}` (CRUD + `/export` + `/{id}/activate`), `/api/tickets` (+ `/export`, `/{id}/assign`, `/{id}/status`, `/{id}/comments`, `/{id}/events`), `/api/dashboard` (+ `/export`), `/api/meta`, `/api/lookups/*`, `/api/health`.

## Demo publicada (Firebase + servidor propio + Cloudflare Tunnel)

**Instalacion aislada en servidor1 y jobs de Jenkins: [docs/SERVIDOR1_JENKINS.md](docs/SERVIDOR1_JENKINS.md).** Usa `deploy/compose.server.yml`, limites propios y un tunel nuevo sin modificar Didaxis, SimFin ni otros servicios existentes.

Guía paso a paso: **[docs/DESPLIEGUE_DEMO.md](docs/DESPLIEGUE_DEMO.md)**. El frontend se sirve en `https://tickets.sivalt.com` (Firebase Hosting) y la API en `https://tickets-api.sivalt.com`, a través del túnel definido en `docker-compose.demo.yml` (sin puertos abiertos en el servidor).

Para este modo con dos orígenes:
- CORS admite credenciales para `CORS_ORIGINS`.
- La cookie de sesión no es de terceros, porque ambos subdominios son del mismo sitio.
- `/auth/refresh` y `/auth/logout` exigen la cabecera `X-Requested-With`, contra peticiones forjadas desde otras páginas, incluidos otros subdominios.

## Preparación para despliegue (Cloudflare / HTTPS)

- **Proxy**: el `nginx` del frontend sirve la SPA y reenvía `/api` al backend (mismo origen → cookie de refresco sin CORS). Cloudflare → tu servidor (nginx/Traefik/Caddy) → contenedor `frontend` (:8080) → `backend` (:8000). Uvicorn corre con `--proxy-headers` (respeta `X-Forwarded-For/Proto`); restringe `FORWARDED_ALLOW_IPS` a tu proxy.
- Define `COOKIE_SECURE=true`, `APP_ENV=production`, un `JWT_SECRET` y contraseñas propios, `ADMIN_EMAIL/ADMIN_PASSWORD` reales; **no** uses el perfil `demo`. La documentación interactiva queda oculta por defecto en producción.
- Cloudflare: modo SSL/TLS **Full (strict)**; IP real vía `CF-Connecting-IP` (plantilla comentada en `frontend/docker/nginx.conf.template`); reglas WAF/rate limiting sobre `/api/auth/login` complementan el límite en memoria.
- Migraciones: ejecutar la imagen del repo *database* como paso previo (ver su README). En producción el servicio `migrate` y `db` de este compose se sustituyen por la base gestionada.
- Imágenes sin privilegios (`api` UID 10001; nginx-unprivileged); sin secretos en Git (`.env` ignorado).

## Limitaciones conocidas

- Un rol por usuario; categorías de catálogo fijo; estatus/transiciones definidos en código.
- Límite de intentos de login en memoria (por proceso); con varias réplicas usar Redis o el WAF.
- La rotación del refresh token puede cerrar la sesión si dos pestañas renuevan exactamente a la vez.
- Fechas agrupadas en UTC en dashboard y CSV (la interfaz muestra fechas en la zona horaria del navegador).
- Sin recuperación de contraseña por correo, registro público, adjuntos ni notificaciones (fuera de alcance).
