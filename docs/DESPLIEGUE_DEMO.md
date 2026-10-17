# Despliegue de la demo: Firebase + servidor propio + Cloudflare Tunnel

```
Navegador
  ├── https://tickets.sivalt.com       → Firebase Hosting (Angular, estático)
  └── https://tickets-api.sivalt.com   → Cloudflare → túnel (cloudflared) → backend:8000 ─ db (Docker, servidor propio)
```

**Por qué dos subdominios de un solo nivel.** Ambos pertenecen al mismo sitio (`sivalt.com`), así que la cookie `HttpOnly` de sesión (`SameSite=Lax`) no es una cookie de terceros: funciona en Chrome, Safari y Firefox. Además, el certificado gratuito de Cloudflare cubre `*.sivalt.com`, pero no subdominios de dos niveles como `api.tickets.sivalt.com`.

**Qué no se expone.** El servidor no abre puertos: `cloudflared` sale hacia Cloudflare. La base de datos y el backend sólo son accesibles en la red interna de Docker.

---

## 1. Servidor: backend + túnel

Requisitos: Docker con Compose v2 y los tres repositorios clonados como carpetas hermanas.

### 1.1 Crear el túnel en Cloudflare

1. Cloudflare → **Zero Trust → Networks → Tunnels → Create a tunnel** → tipo *Cloudflared* → nombre `tickets-demo`.
2. En «Install connector» copia el **token** (lo que va tras `--token`). No lo instales en el sistema: lo usa el contenedor `cloudflared`.
3. **Public Hostname → Add**:
   - Subdomain `tickets-api`, Domain `sivalt.com`.
   - Service: tipo `HTTP`, URL `backend:8000`. Es el nombre del servicio dentro de Docker.
   - Cloudflare crea solo el registro DNS (CNAME «proxied»).

### 1.2 Variables y arranque

```bash
cd consultora-tickets-backend
cp .env.demo.example .env.demo
```

Rellena todo `.env.demo`:

| Variable | Valor |
|---|---|
| `POSTGRES_PASSWORD` | Aleatoria. |
| `JWT_SECRET` | ≥ 32 caracteres: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_PASSWORD`, `DEMO_PASSWORD` | ≥ 12 caracteres y **distintas** de las de `.env.example`: el seed las rechaza. Son las que compartirás con los evaluadores. |
| `CORS_ORIGINS` | `https://tickets.sivalt.com` (añade `,https://TU-PROYECTO.web.app` si también vas a usar esa URL). |
| `CLOUDFLARE_TUNNEL_TOKEN` | El token del paso 1.1. |

Arranque:

```bash
docker compose -f docker-compose.demo.yml --env-file .env.demo up -d --build
docker compose -f docker-compose.demo.yml --env-file .env.demo ps          # db, backend y cloudflared «healthy/running»
docker compose -f docker-compose.demo.yml --env-file .env.demo logs cloudflared | tail   # «Registered tunnel connection»
```

El job `migrate` aplica las migraciones y carga el administrador y los datos demo. Es idempotente: si ya hay tickets no los vuelve a cargar.

El backend arranca con `APP_ENV=demo`, que aplica las mismas protecciones que producción:
- No arranca con el `JWT_SECRET` de ejemplo ni sin cookie `Secure`.
- `/api/docs` queda oculto, salvo `DOCS_ENABLED=true`.
- La IP del visitante se toma de `CF-Connecting-IP`.

Todos los servicios tienen `restart: unless-stopped`, así que se recuperan solos tras reiniciar el servidor o Docker.

## 2. Frontend en Firebase Hosting

Requisitos: Node 22 y una cuenta de Google con un proyecto de Firebase.

```bash
cd consultora-tickets-frontend
npm ci
npx firebase-tools@15.32.1 login                     # abre el navegador (una sola vez)
npx firebase-tools@15.32.1 use --add                 # elige el proyecto; crea .firebaserc (no se versiona)
API_URL=https://tickets-api.sivalt.com/api npm run build:firebase
npm run deploy:firebase
```

`build:firebase` compila en producción y genera:
- `config.js`, con la URL de la API.
- `firebase.json`, con:
  - *Fallback* de la SPA a `index.html`.
  - CSP con `connect-src` limitado a `https://tickets-api.sivalt.com`.
  - HSTS y las demás cabeceras de seguridad.
  - Caché de un año para los bundles con hash, y `no-store` para `index.html`, `config.js` y las rutas.

En PowerShell la variable se define así: `$env:API_URL="https://tickets-api.sivalt.com/api"; npm run build:firebase`.

### 2.1 Dominio `tickets.sivalt.com`

1. Firebase Console → **Hosting → Add custom domain** → `tickets.sivalt.com`.
2. En Cloudflare → DNS, crea los registros que indique Firebase (A/TXT o CNAME) con el **proxy desactivado (nube gris, «DNS only»)**. Firebase necesita ver el tráfico directo para emitir su certificado y ya sirve con CDN y HTTPS propios.
3. Espera a que Firebase marque el dominio como «Connected» (minutos a unas horas).

## 3. Comprobaciones antes de la demo

```bash
# API viva a través del túnel
curl -s https://tickets-api.sivalt.com/api/health                       # {"status":"ok"}
# CORS: sólo el origen del frontend
curl -si -X OPTIONS https://tickets-api.sivalt.com/api/auth/refresh \
  -H "Origin: https://tickets.sivalt.com" -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: x-requested-with" | grep -i access-control-allow
curl -si -X OPTIONS https://tickets-api.sivalt.com/api/auth/refresh \
  -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST" | grep -ic access-control-allow-origin   # 0
# Cookie de sesión: HttpOnly; Secure; SameSite=Lax; Path=/api/auth
curl -si https://tickets-api.sivalt.com/api/auth/login -H "content-type: application/json" \
  -d '{"email":"admin@consultora.local","password":"TU_ADMIN_PASSWORD"}' | grep -i set-cookie
# Cabeceras del frontend (CSP con connect-src a la API, no-store en index)
curl -sI https://tickets.sivalt.com/tickets | grep -iE "content-security|cache-control|strict-transport"
# La documentación no debe ser pública
curl -s -o /dev/null -w "%{http_code}\n" https://tickets-api.sivalt.com/api/docs                    # 404
```

En el navegador, comprueba también:
- Que el login funciona y que **al recargar la página la sesión se mantiene** (en Safari y Firefox también).
- Que exportar a CSV descarga el archivo.
- Que «Cambiar contraseña» funciona.

Las pruebas E2E se pueden lanzar contra la demo publicada:

```bash
E2E_BASE_URL=https://tickets.sivalt.com E2E_ADMIN_PASSWORD=... E2E_READER_PASSWORD=... npm run e2e
```

Las pruebas crean registros con prefijo «QA»/«E2E»; mejor lanzarlas antes de entregar las credenciales.

## 4. Recomendaciones de Cloudflare (plan gratuito)

- **Rate limiting** (Security → WAF → Rate limiting rules): por ejemplo, 10 peticiones/minuto por IP a `tickets-api.sivalt.com/api/auth/login`. Complementa el límite del backend.
- **Bot Fight Mode** activado.
- Opcional: **Cloudflare Access** sobre `tickets.sivalt.com` no aplica (lo sirve Firebase). Para restringir la API a los evaluadores habría que exentar los *preflight* CORS; para una demo corta basta con contraseñas propias y el rate limiting.
- SSL/TLS → modo **Full** (el túnel ya cifra hasta el servidor).

## 5. Después de la demo

```bash
docker compose -f docker-compose.demo.yml --env-file .env.demo stop cloudflared   # corta el acceso público al instante
# o todo, conservando los datos:
docker compose -f docker-compose.demo.yml --env-file .env.demo down
```

En Cloudflare puedes borrar el túnel, lo que invalida su token. Si las credenciales demo se compartieron fuera, cámbialas antes de volver a publicar.

## Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| Error CORS en la consola | `CORS_ORIGINS` no coincide exactamente con el origen (esquema, subdominio, sin barra final). Reinicia `backend` tras cambiarlo. |
| La sesión se pierde al recargar | El frontend se abre en `*.web.app` (otro sitio) en lugar de `tickets.sivalt.com`, o falta `withCredentials` (no debería, lo pone el interceptor). |
| `403 Falta la cabecera X-Requested-With` | Una petición a `/auth/refresh` o `/auth/logout` hecha fuera de la app (p. ej. con curl). Añade `-H "X-Requested-With: x"`. |
| Bloqueos de login a todos los usuarios | `CLIENT_IP_HEADER` vacío detrás del túnel: todos comparten la IP de cloudflared. El compose demo ya lo define. |
| `backend` no arranca: «Configuración insegura» | `JWT_SECRET` de ejemplo o cookie no `Secure` con `APP_ENV=demo`. |
| `migrate` termina con error de contraseña de ejemplo | `ADMIN_PASSWORD`/`DEMO_PASSWORD` iguales a las de `.env.example`. |
