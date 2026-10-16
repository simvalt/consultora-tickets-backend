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
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_SECONDS` | Límite de intentos fallidos por IP+correo |
| `DOCS_ENABLED` | Publica/oculta `/api/docs` y `/api/openapi.json` |
| `FORWARDED_ALLOW_IPS` | Proxies de confianza para `X-Forwarded-*` |

## Pruebas, lint y compilación

Las pruebas usan un **PostgreSQL real**; el esquema se crea ejecutando las migraciones Alembic del repo *database* (el backend no tiene copia del esquema). Con el contenedor `db` arriba:

```bash
pip install -r requirements-dev.txt
pytest                      # 167 pruebas (usa la base tickets_test; se crea sola)
ruff check .                # lint
docker build -t tickets-backend .   # compilación de la imagen
```

Variables: `TEST_DATABASE_URL` (por defecto `postgresql://tickets:change_me_local@127.0.0.1:5432/tickets_test`) y `DATABASE_REPO_PATH` (por defecto `../consultora-tickets-database`). Cubren: login correcto/incorrecto/inactivo y rotación de refresh; escalera de permisos por módulo y rechazo de operaciones no autorizadas; anti-escalada de privilegios; protección del último administrador; las 49 combinaciones de transiciones; motivo/resolución obligatorios; historial; filtros/orden/paginación; CSV idéntico al listado (más de una página) y protección contra fórmulas; métricas con datos conocidos.

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
- **Access token** JWT (15 min) que el frontend guarda sólo en memoria. **Refresh token** opaco, aleatorio, en cookie `HttpOnly; Secure; SameSite=Lax; Path=/api/auth`; en BD sólo se guarda su SHA-256. Se **rota en cada renovación** (el anterior se revoca) y `POST /api/auth/logout` lo revoca. Cambiar contraseña/rol o desactivar a un usuario revoca sus refresh tokens.
- Al expirar la sesión (o fallar la renovación) el frontend regresa al login.
- Límite de intentos fallidos (5 por 5 min por IP+correo, en memoria).

## Permisos

Módulos: `tickets`, `users`, `roles`, `clients`, `dashboard`. Niveles **acumulativos** por rol y módulo: 0 Nada · 1 Leer · 2 Insertar (leer+crear) · 3 Editar (+modificar) · 4 Eliminar (+eliminar/desactivar).

| Operación | Nivel mínimo |
|---|---|
| Listar/ver/exportar CSV de un módulo | Leer |
| Crear (ticket, usuario, rol, cliente) | Insertar |
| Editar; **cambiar estatus, asignar, comentar, reabrir tickets**; reactivar | Editar |
| Eliminar ticket (lógico) / desactivar usuario, rol o cliente | Eliminar |
| Crear un ticket **con responsable** | Editar |

Reglas adicionales (todas en el backend):

- **Administrador** = usuario activo con rol activo y nivel 4 en todos los módulos. Ninguna operación (desactivar usuario, cambiar su rol, editar/desactivar un rol) puede dejar el sistema sin uno (`409 last_admin`; las operaciones se serializan con un advisory lock).
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

`GET /api/<módulo>/export` acepta **los mismos filtros y orden** que el listado y devuelve **todos** los registros coincidentes (sin paginar). Requiere nivel Leer. UTF-8 con BOM, CRLF, comillas/comas/saltos de línea escapados, y valores que empiezan con `= + - @` o tab/CR se prefijan con `'` (anti inyección de fórmulas). Fechas en ISO-8601 UTC. Columnas explícitas:

- **tickets**: Folio, Título, Descripción, Cliente, Categoría, Prioridad, Estatus, Creado por, Responsable, Creado, Modificado, Inicio de atención, Cierre, Vencimiento, Resolución
- **users**: Nombre, Correo, Rol, Activo, Creado, Último acceso (sin hashes ni tokens)
- **roles**: Rol, Descripción, Activo, Usuarios, y un nivel por módulo · **clients**: Nombre, Contacto, Correo, Teléfono, Activo, Creado · **dashboard**: Sección, Elemento, Valor

## API

OpenAPI interactivo en `/api/docs`. Errores uniformes: `{"error": {"code", "message", "details"}}` (sin trazas). Resumen: `/api/auth/{login,refresh,logout,me}`, `/api/{users,roles,clients}` (CRUD + `/export` + `/{id}/activate`), `/api/tickets` (+ `/export`, `/{id}/assign`, `/{id}/status`, `/{id}/comments`, `/{id}/events`), `/api/dashboard` (+ `/export`), `/api/meta`, `/api/lookups/*`, `/api/health`.

## Preparación para despliegue (Cloudflare / HTTPS)

- **Proxy**: el `nginx` del frontend sirve la SPA y reenvía `/api` al backend (mismo origen → cookie de refresco sin CORS). Cloudflare → tu servidor (nginx/Traefik/Caddy) → contenedor `frontend` (:8080) → `backend` (:8000). Uvicorn corre con `--proxy-headers` (respeta `X-Forwarded-For/Proto`); restringe `FORWARDED_ALLOW_IPS` a tu proxy.
- Define `COOKIE_SECURE=true`, `APP_ENV=production`, un `JWT_SECRET` y contraseñas propios, `ADMIN_EMAIL/ADMIN_PASSWORD` reales; **no** uses el perfil `demo`. Opcional `DOCS_ENABLED=false`.
- Cloudflare: modo SSL/TLS **Full (strict)**; IP real vía `CF-Connecting-IP` (plantilla comentada en `frontend/docker/nginx.conf.template`); reglas WAF/rate limiting sobre `/api/auth/login` complementan el límite en memoria.
- Migraciones: ejecutar la imagen del repo *database* como paso previo (ver su README). En producción el servicio `migrate` y `db` de este compose se sustituyen por la base gestionada.
- Imágenes sin privilegios (`api` UID 10001; nginx-unprivileged); sin secretos en Git (`.env` ignorado).

## Limitaciones conocidas

- Un rol por usuario; categorías de catálogo fijo; estatus/transiciones definidos en código.
- Límite de intentos de login en memoria (por proceso); con varias réplicas usar Redis o el WAF.
- La rotación del refresh token puede cerrar la sesión si dos pestañas renuevan exactamente a la vez.
- Fechas agrupadas en UTC en dashboard y CSV (la interfaz muestra fechas en la zona horaria del navegador).
- Sin recuperación de contraseña por correo, registro público, adjuntos ni notificaciones (fuera de alcance).
