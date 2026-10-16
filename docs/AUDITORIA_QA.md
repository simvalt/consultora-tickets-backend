# Auditoría de seguridad, calidad de código y QA

Fecha: 2026-10-16. Alcance: los tres repositorios (`database`, `backend`, `frontend`) y su despliegue con Docker Compose y nginx.

## Método

1. **Revisión de código** completa (backend, frontend, migraciones, Dockerfiles, compose, nginx).
2. **Sondeo activo** contra el stack en marcha (a través de nginx, como lo vería un cliente): tokens manipulados, inyección, NUL, asignación masiva, *spoofing* de IP, carreras, cabeceras, CORS, tamaños.
3. **Auditoría de dependencias**: `pip-audit` (Python) y `npm audit` (Node).
4. **Corrección** de cada hallazgo, con una prueba automatizada que lo cubre.
5. **QA** por capas: unitarias, integración API, seguridad, casos límite, concurrencia, carga con 200 000 tickets, migraciones/seeds y E2E en navegador (escritorio y móvil).

## Hallazgos y estado

Severidad: **Alta**, **Media**, **Baja** e **Info**. Todos están corregidos salvo los de la sección «Riesgos residuales».

| ID | Sev. | Hallazgo | Evidencia antes | Corrección |
|---|---|---|---|---|
| A-01 | Alta | **Dependencias con vulnerabilidades conocidas**: PyJWT 2.10.1 (16 avisos), Starlette 0.41.3 (7 avisos), pytest (1) y Playwright 1.49.1 (GHSA-7mvr-c777-76hp, dev). | `pip-audit`: 24 avisos; `npm audit`: 2 altas | FastAPI 0.143.3, Starlette 1.8.0, PyJWT 2.15.1, uvicorn 0.54, SQLAlchemy 2.1.4, psycopg 3.3.6, pydantic 2.14, argon2-cffi 25.1, pytest 9.1 y Playwright 1.64.1. `pip-audit` y `npm audit` ya no reportan nada. |
| A-02 | Alta | **Commit después de responder**. Con FastAPI moderno, las dependencias con `yield` usan por defecto el alcance «request» y su cierre corre *después* de enviar la respuesta. El cliente podía recibir 200 aunque el COMMIT fallara. Se detectó al actualizar. | Docstring de `Depends(scope=…)` | `DB = Depends(get_db, scope="function")` en todos los endpoints (`app/db.py`): la transacción se confirma antes de responder. |
| A-03 | Media | **Evasión del límite de intentos de login falseando `X-Forwarded-For`**: nginx concatenaba la cabecera del cliente y uvicorn confiaba en ella. | 8 intentos fallidos rotando XFF: 8 × 401, ningún 429 | nginx **sobrescribe** `X-Forwarded-For` con `$remote_addr` (y documenta `real_ip` para Cloudflare). Después: el 6.º intento devuelve 429. |
| A-04 | Media | **Límite sólo por IP+correo**: no frenaba ni el *credential stuffing* (muchas cuentas desde una IP) ni el ataque distribuido (una cuenta desde muchas IPs). | 7 correos distintos desde una IP: 7 × 401 | Tres ventanas: IP+correo (5), IP (20) y correo (10), configurables. Se registran en log los fallos (correo enmascarado) y los bloqueos. |
| A-05 | Media | **Cambio de la propia contraseña sin pedir la actual** (`PATCH /users/{id}`), y los access tokens emitidos antes seguían válidos tras cambiarla o restablecerla. | PATCH propio → 200; token anterior → 200 | Nuevo `POST /api/auth/change-password` (exige la actual y la nueva debe ser distinta). El PATCH sobre uno mismo devuelve 403. Columna `users.password_changed_at` (migración 0003): se rechazan los tokens con `iat` anterior y se revocan todos los refresh tokens. |
| A-06 | Media | **Carácter NUL → error 500**, tanto en el cuerpo como en `?q=`: PostgreSQL rechaza `\x00` en texto. | `POST /clients {"name":"a\u0000b"}` → 500 | Base `Input` de los esquemas que rechaza NUL (422), más un manejador de `DataError` → 400. |
| A-07 | Baja | **Carreras en valores únicos** (dos altas con el mismo nombre a la vez) podían terminar en 500 por `IntegrityError`. | Defensivo (no se reprodujo) | Manejador de `IntegrityError` → 409 `conflict`. Prueba con 10 altas simultáneas: un 201 y nueve 409. |
| A-08 | Baja | **Campos desconocidos ignorados en silencio**: amplía la superficie de asignación masiva y oculta errores del cliente (p. ej. `PATCH /tickets {"status": …}` respondía 200 sin hacer nada). | `PATCH {"status","deleted_at"}` → 200 | `extra="forbid"` en todos los cuerpos de entrada → 422. Los nulos se rechazan en campos que no admiten nulo. |
| A-09 | Baja | **Fechas sin zona horaria** interpretadas según la zona de la sesión de la BD. | `due_at: "2031-01-01T10:00:00"` | Se interpretan explícitamente como UTC (validador). |
| A-10 | Baja | **Respuestas de la API sin `Cache-Control`**: los datos de usuarios autenticados podían quedar en cachés. | Cabecera ausente | `Cache-Control: no-store` en todo `/api/*`, más `X-Frame-Options: DENY`. |
| A-11 | Baja | **Producción podía arrancar con valores inseguros**: `JWT_SECRET` de ejemplo, cookie sin `Secure`, y `/api/docs` público porque el compose forzaba `DOCS_ENABLED=true`. | Revisión de config | Validación al arrancar con `APP_ENV=production` (falla si el secreto es el de ejemplo o `COOKIE_SECURE=false`). La documentación se oculta por defecto en producción (`env_ignore_empty`). |
| A-12 | Baja | **`returnUrl` del login aceptaba `//dominio`**. | Revisión de código | `safeReturnUrl()`: sólo admite rutas internas. Hay prueba unitaria y E2E. |
| A-13 | Media (operación) | Al fijar `postgres:16.15`, la imagen pasó a Debian 13 (glibc 2.41). El volumen se había creado con glibc 2.36: **collation desalineada**. Arriesga índices de texto inválidos (unicidad de correo y nombre) y hace fallar `CREATE DATABASE`. | Error *collation version mismatch* | Fijado `postgres:16.15-bookworm` (glibc 2.36). Ver «Actualizar PostgreSQL» en el README del repo *database*. |
| A-14 | Info | El limitador en memoria crecía sin límite con claves caducadas. | Revisión | Purga al superar 50 000 claves. Hay prueba de memoria acotada. |
| A-15 | Info | Cabeceras de seguridad duplicadas en `/api` (backend + nginx). | `curl -I` | `proxy_hide_header` en nginx. |
| A-16 | Info | Imágenes base sin versión de parche (`python:3.12-slim`, `node:22-alpine`, nginx 1.27 ya superado). | Revisión | `python:3.12.15-slim`, `node:22.23.2-alpine`, `nginx-unprivileged:1.30.6-alpine`, `postgres:16.15-bookworm`. |
| A-17 | Info | Sin registro de eventos de seguridad. | Revisión | Logs de login fallido, bloqueo, usuario inactivo y cambio de contraseña. Nunca se registran contraseñas ni tokens. |

Además, la revisión manual comprobó sin hallazgos: SQL siempre parametrizado (orden con lista blanca); JWT con algoritmo fijo (se rechazan `alg:none`, HS512, firma ajena, expirados, sin `iat` y `sub` manipulado); refresh opaco en cookie `HttpOnly; SameSite=Lax; Path=/api/auth` con sólo su hash en BD; argon2id con hash de relleno contra enumeración por tiempo; errores sin trazas; CORS restringido a la lista configurada; CSP estricta (`script-src 'self'`); contenedores sin root; secretos fuera de Git.

## Código mejorado (sin cambio funcional)

- `services/tickets.update`: se eliminó el filtrado manual de `None`; ahora el esquema garantiza los nulos válidos y el servicio usa `exclude_unset`.
- `repositories/roles.update`: se quitaron una copia innecesaria y una rama duplicada. En `count_active_admins` el import salió de la función.
- Se eliminó código muerto (`me_payload`). `Page` usa genéricos PEP 695. El helper `_exec` de las pruebas quedó legible. Se cambió a `httpx2` (Starlette deprecó `httpx` en `TestClient`).
- Frontend: el login ya no tiene una rama de error redundante. `PagedList` vuelve al orden por defecto al quitar el orden de una columna (antes conservaba la columna con la dirección «asc»). Se corrigió el orden de declaración en `TicketForm`. En el diálogo de usuario se oculta la contraseña propia y se remite a «Cambiar contraseña».

## Optimizaciones medidas

Volumen de prueba: **200 000 tickets**, 300 000 eventos, 100 000 comentarios, 300 clientes y 51 usuarios (`scripts/qa_performance.py`). Las cifras son medianas de varias ejecuciones contra la API.

| Operación | Antes | Después | Cambio |
|---|---|---|---|
| Listado de tickets, página 1 (orden por defecto) | 103 ms | **19 ms** | Índice `(created_at DESC, id)` parcial (migración 0004) y total contado sobre la tabla base, sin los JOIN de presentación. |
| Exportación CSV completa (200 000 filas, 39 MB) | 8,2 s | **5,6 s** | Formateo de celdas con ruta rápida por tipo exacto (el formateo pasó de 3,8 s a 1,8 s; la consulta tarda 1,5 s). |
| Exportación CSV filtrada (50 000 filas) | 2,1 s | **1,4 s** | Ídem. |
| Autenticación en cada solicitud | 3 consultas | **1 consulta** (≈5 ms `/auth/me`) | Usuario, rol, permisos y overrides con `json_object_agg`. |
| Indicadores del dashboard | 4 recorridos | **1 recorrido** | Agregación por (estatus, prioridad) con `FILTER`, reducida en Python. |
| Búsqueda por folio parcial | escaneo | **21 ms** | Índice trigram en `folio` (migración 0003). |

Sin cambios (adecuado para el volumen):

| Operación | Tiempo | Comentario |
|---|---|---|
| Detalle e historial | 6–7 ms | |
| Filtros combinados | 9 ms | |
| Vencidos ordenados | 27 ms | Usa el índice `ix_tickets_due_at`. |
| Dashboard de 30 días | ≈140 ms | Sus indicadores, sin periodo, cubren todo el histórico. |
| Dashboard de un año | 215 ms | |
| Búsqueda de texto muy común | 230 ms | Coincide con el 20 % de las filas: PostgreSQL elige escaneo paralelo, que es lo correcto. |
| Página 5 000 | 134 ms | `OFFSET` profundo; ver riesgos residuales. |

## QA ejecutado

| Capa | Pruebas | Resultado |
|---|---|---|
| Backend: funcionales (auth, permisos, tickets, listados/CSV, dashboard, overrides) | 183 | ✅ |
| Backend: **seguridad** (`test_qa_security.py`): 9 tipos de token inválido, payload manipulado, rol inactivo, cambio y restablecimiento de contraseña, 3 límites de intentos y memoria acotada, NUL en cuerpo y query, asignación masiva (6 casos), 7 cargas de inyección, 8 parámetros malformados, XSS almacenado, IDs fuera de rango, tamaños máximos, JSON roto, cabeceras, cookie, configuración de producción, CORS y fuga de trazas | 61 | ✅ |
| Backend: **casos límite** (`test_qa_edge.py`): 10 órdenes × 2 direcciones paginando sin huecos ni duplicados, CSV de 1 203 filas (cruza bloques de 500), unicode/emoji, folio parcial, límites de fechas UTC, vencimiento sin zona, responsable inactivo, ticket eliminado invisible en 7 rutas, matriz de parámetros del dashboard, coherencia dashboard ↔ listado, reapertura y doble cierre, permisos aplicados sin re-login | 47 | ✅ |
| Backend: **concurrencia** (`test_qa_concurrency.py`): 8 cambios de estatus simultáneos (gana 1), dos admins desactivándose a la vez (queda ≥ 1), 10 altas con nombre duplicado (ningún 500), 20 comentarios, 6 renovaciones con el mismo refresh (rota 1), 25 folios únicos | 6 | ✅ |
| **Total backend** | **297** | ✅ (≈4 min) |
| Frontend: unitarias (Vitest): auth e interceptor, guards, `PagedList`, utilidades, editor de permisos matriz/JSON, `safeReturnUrl` (8 casos), cambio de contraseña, flujo principal por componentes | 46 | ✅ |
| Frontend: **E2E** (Playwright, stack Docker real con datos demo limpios): flujo principal; sesión (recarga, logout + atrás, cookie borrada, `returnUrl` interno y externo, Enter, validaciones, usuario inactivo, menú por rol); tickets (crear y editar, rechazo, reapertura, eliminar con cancelar y confirmar, filtros, orden, paginación, vacío, CSV con BOM igual al filtro); administración (cliente duplicado y desactivación, rol por JSON → usuario nuevo → acceso, propio rol y usuario protegidos, cambio de contraseña); dashboard (filtros, vacío); móvil 375 px sin desplazamiento horizontal | 23 | ✅ |
| Migraciones | `upgrade` → `downgrade base` → `upgrade` (0001–0004) en BD nueva; seeds idempotentes; seed demo bloqueado con `APP_ENV=production` y contraseña corta | ✅ |
| Carga | 200 000 tickets, 14 operaciones medidas y planes de ejecución revisados | ✅ |
| Dependencias | `pip-audit` y `npm audit` | ✅ sin vulnerabilidades |
| Exploratoria manual | Navegador integrado, escritorio y móvil: consola sin errores salvo el 401 esperado de `/auth/refresh` al arrancar sin sesión | ✅ |

**Incidencias durante el QA.** Una prueba E2E («menú por rol») se trabó 60 s y se detuvo por *timeout*. La causa fue que mis propios sondeos de fuerza bruta habían bloqueado la cuenta `supervisor@` durante la ventana de 5 minutos: el límite nuevo funcionando. Pasó al reiniciar el limitador. Otras dos fallaron por sincronización de la prueba (exportar antes del *debounce* del filtro y abrir un diálogo mientras el anterior seguía cerrándose); se corrigieron las pruebas, no la aplicación. La ejecución final, sobre datos demo regenerados, pasó 23/23.

## Riesgos residuales y recomendaciones

| Riesgo | Recomendación |
|---|---|
| El limitador de intentos vive en memoria de cada proceso. | Con varias réplicas: Redis, o *rate limiting* del WAF de Cloudflare sobre `/api/auth/login`. |
| Sin detección de reutilización de refresh tokens (detectaría robos). Se omitió porque dos pestañas que renuevan a la vez cerrarían la sesión. | Implementar familias de tokens con periodo de gracia si el riesgo lo justifica. |
| CSP con `style-src 'unsafe-inline'`, que exige Angular Material. | Aceptable: no se permite script inline. Revisar con *nonces* si Angular lo habilita. |
| HSTS no se emite desde nginx (el TLS termina en Cloudflare). | Activar HSTS en Cloudflare (SSL/TLS → Edge Certificates). |
| La búsqueda distingue acentos («accion» no encuentra «acción»). | Extensión `unaccent` con un índice de expresión inmutable. |
| Paginación con `OFFSET` (página 5 000 ≈ 134 ms). | Paginación por cursor si se necesitan páginas muy profundas. |
| Las dependencias se fijan por versión, no por hash. | `pip install --require-hashes` y lockfile de pip en CI. |
| No hay bitácora persistente de cambios administrativos (roles, permisos, usuarios); sólo los tickets tienen historial. | Tabla `audit_log` si se exige trazabilidad de administración. |
| Precisión de 1 s al invalidar tokens tras un cambio de contraseña. | Aceptable; podría usarse un `jti` o una versión de token. |
| TypeScript 7 disponible; se mantiene 6.0 por compatibilidad con Angular 22. | Actualizar cuando Angular lo soporte. |

## Cómo reproducir

```bash
# backend (con la BD del compose arriba)
pip install -r requirements-dev.txt
pytest                                    # 297 pruebas
ruff check . && pip-audit -r requirements.txt
python scripts/qa_performance.py          # carga de 200 000 tickets en la BD tickets_perf y medición
# frontend
npm test && npm run lint && npm run build && npm audit
npm run e2e                               # con el stack de docker compose --profile demo en marcha
```
