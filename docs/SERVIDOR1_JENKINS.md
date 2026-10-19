# Tickets en servidor1: instalacion aislada y reversible

## Regla del propietario

**No modificar aplicaciones, contenedores, redes, volumenes, tuneles, archivos ni ajustes existentes de servidor1.** Didaxis y SimFin quedan fuera del alcance. La unica excepcion autorizada es **agregar jobs de Tickets al Jenkins actual**. No cambiar sus jobs actuales, plugins, nodos, numero de ejecutores ni configuracion global.

## Recursos exclusivos de Tickets

| Recurso | Nombre / ruta |
|---|---|
| Directorio nuevo | `/home/simonvalt/tickets-demo` |
| API + PostgreSQL | proyecto Compose `consultora-tickets-demo` |
| Pruebas efimeras de database | proyecto Compose `consultora-tickets-ci` |
| Validaciones estaticas de backend | proyecto Compose `consultora-tickets-backend-ci` |
| Redes de la demo | `consultora-tickets-demo_data` (interna), `consultora-tickets-demo_edge` |
| Datos | volumen `consultora-tickets-demo_pgdata` |
| Secretos | `state/runtime.env` (0600, generado en el servidor, fuera de Git) |
| Version desplegada | `state/release.env` (SHAs completos de API y migraciones) |
| Respaldos previos a actualizar | `state/backups/*.dump` (0600) |
| Proyecto Firebase nuevo | `sivalt-tickets-demo` (Spark) |
| Identidad Hosting nueva | `jenkins-tickets-hosting@sivalt-tickets-demo.iam.gserviceaccount.com` |
| Tunel nuevo | `tickets-demo` (`bd996162-c96e-487b-8c8e-96a6bc1de4a0`), exclusivamente para `tickets-api.sivalt.com` |
| Jobs nuevos | `Tickets-backend`, `Tickets-database`, `Tickets-frontend` |
| Permiso NUEVO para Jenkins | `/etc/sudoers.d/tickets-demo` |

No se publica ningun puerto del host ni se conecta Tickets a `server-net`, `jenkins-net` o las redes actuales. El tunel del host llamado `servidor1` no se modifica ni se reutiliza. La API solo es accesible por su nuevo conector en la red `edge`; PostgreSQL solo por la red privada `data`.

Limites de ejecucion: PostgreSQL 768 MiB / 1 CPU; API 512 MiB / 1 CPU; migraciones 512 MiB / 1 CPU; conector 128 MiB / 0.25 CPU. Logs con rotacion 3 x 10 MiB. CI usa una BD desechable en memoria (512 MiB) y un contenedor de pruebas con 1536 MiB / 1.5 CPU. Las construcciones de API y migraciones se solicitan por separado. El frontend ejecutado por Jenkins limita el heap de Node a 1536 MiB; no es un limite global de memoria del proceso. Las compilaciones comparten recursos del host y no tienen aislamiento de CPU completo.

## Primera instalacion

1. Ejecutar `scripts/prepare-server.py` como `simonvalt` en servidor1. Solo crea el directorio nuevo, clona los tres repositorios publicos y genera contrasenas aleatorias distintas de las de ejemplo. Guarda una referencia de identidad y arranque de los contenedores anteriores sin guardar su configuracion sensible.
2. Ejecutar `bash ~/tickets-demo/consultora-tickets-backend/scripts/deploy-server.sh`. Construye las imagenes con sus commits completos, inicia la BD, migra y carga demo de manera idempotente; despues inicia la API y comprueba su salud. No arranca el tunel.
3. Ejecutar `python3 ~/tickets-demo/consultora-tickets-backend/scripts/verify-existing-services.py` para comprobar que los contenedores previos no fueron reemplazados ni reiniciados.
4. Consultar credenciales solo en `state/runtime.env`. No copiarlas a Git, logs ni al README publico. La URL `*.web.app` permite revisar la descarga del frontend; para autenticarse usar `tickets.sivalt.com` con la API del mismo sitio.

## Jenkins: solo jobs nuevos

Cada job es de tipo **Pipeline**, definicion **Pipeline script from SCM**, Git, URL publica de su repositorio `https://github.com/simvalt/consultora-tickets-<componente>.git`, rama `*/main`, ruta `Jenkinsfile`. No requiere credencial GitHub para leer repositorios publicos.

El primer build registra la consulta de cambios (`pollSCM`, aproximadamente cada cinco minutos). No se cambia el webhook, la GitHub App ni la configuracion global actual. Los jobs usan la etiqueta ya existente `servidor1` y su unico ejecutor: se ejecutan en cola. Cada job evita ejecuciones concurrentes propias y conserva diez builds.

### API y migraciones

Instalar `deploy/jenkins-run.sh` en `~/tickets-demo/automation/run.sh`, propietario `simonvalt`, modo 0700. Crear `~/tickets-demo/releases`. Conserva la interfaz `database COMMIT` del job existente y agrega `backend COMMIT FASE BUILD_NUMBER` para el backend.

**Tickets-backend no ejecuta pruebas ni instala pytest en su imagen de validaciones.** Sus etapas visibles son: checkout, preparar release, lint, auditar dependencias, construir imagenes, respaldar PostgreSQL, aplicar migraciones, desplegar API, verificar salud y verificar aislamiento. Un build correcto termina SUCCESS; se elimina la conversion de pruebas fallidas a UNSTABLE. Lint, auditoria, construccion, respaldo, migraciones y salud permanecen bloqueantes.

El backend usa `scripts/ci-backend.sh`, `deploy/quality.Dockerfile` y el proyecto independiente `consultora-tickets-backend-ci`, sin BD efimera ni pytest. `requirements-ci.txt` contiene las herramientas estaticas y es compartido por `requirements-dev.txt`; las dependencias de pruebas siguen disponibles para desarrollo.

Una release fija conserva los mismos commits entre todas las fases. `flock` serializa cada operacion y `state/backend-pipeline.state` reserva la release para su SHA/build, bloquea intercalaciones con database y exige el orden de las etapas. `post/always` verifica servicios ajenos, limpia exclusivamente el proyecto de validacion backend y libera la reserva, incluso si una etapa falla. Las releases y respaldos se conservan. Si el agente desaparece y no ejecuta post/always, no se elimina la reserva automaticamente: primero comprobar que ese build ya no esta activo y recuperar exclusivamente el estado de Tickets. No borrar bloqueos mientras haya una ejecucion en curso.

**Tickets-database conserva sus pruebas bloqueantes** con `TICKETS_TEST_FAILURE_MODE=strict` y `scripts/ci-server.sh`; su Jenkinsfile no cambia. Un cambio de migraciones sigue verificando compatibilidad antes de aplicarse a la demo. Las validaciones manuales del backend tampoco se eliminan.

Al cambiar el runner hay que instalar su nueva version en `automation/run.sh` antes de publicar el Jenkinsfile. El job no actualiza ese archivo y no se requieren cambios en sudoers, plugins, nodos o configuracion global. El runner rechaza acciones desconocidas, fases fuera de orden y fases de otro build. El despliegue manual de Tickets se rechaza mientras exista una reserva de pipeline activa.

El usuario debe ejecutar una vez:

```sh
sudo bash /home/simonvalt/tickets-demo/install-jenkins-permission.sh
```

El instalador **solo crea** `/etc/sudoers.d/tickets-demo`, valida su sintaxis y se niega a sobrescribir un archivo existente. Permite a `jenkins-agent` invocar el comando de Tickets como `simonvalt`, sin sudo general y sin acceso directo al socket Docker desde el workspace. Ese comando tiene capacidad de despliegue: solo se debe permitir que codigo confiable llegue a `main`. Las credenciales de la demo permanecen en el servidor.

Los tests usan una base efimera; `upgrade -> downgrade base -> upgrade` ocurre **solo en ella**. El script de CI elimina exclusivamente su proyecto `consultora-tickets-ci`. No ejecutar `prune`, reiniciar Docker ni usar `down` sobre otros proyectos.

### Frontend sin claves permanentes

El job usa en modo lectura la credencial OIDC dinamica existente `gcp-wif-oidc`. No se cambia esa credencial ni su proveedor. Se agrego una cuenta de servicio solo al nuevo proyecto, con `roles/firebasehosting.admin`. Su politica acepta exclusivamente el sujeto:

```
https://jenkins.sivalt.com/job/Tickets-frontend/
```

`scripts/firebase-wif.mjs` genera una configuracion efimera fuera del repo; Firebase se ejecuta con `--project sivalt-tickets-demo --only hosting`. No se reutilizan identidades, tokens ni permisos de despliegue de Didaxis o SimFin. No generar una clave JSON de cuenta de servicio. No usar `FIREBASE_TOKEN`.

## Publicacion: SOLO registros nuevos

- Firebase: agregar `tickets.sivalt.com` al sitio nuevo y copiar exactamente los registros de verificacion y destino que indique Firebase. Agregar solo registros para ese subdominio; no cambiar registros actuales. El registro de Hosting debe ser **solo DNS** (nube gris).
- Cloudflare: crear el tunel **nuevo** `tickets-demo`; guardar su token en la variable `CLOUDFLARE_TUNNEL_TOKEN` de `state/runtime.env` sin mostrarlo. Ruta publica nueva: `tickets-api.sivalt.com` -> `http://backend:8000`. No agregar rutas al tunel `servidor1`.
- Con `BACKEND_REF` y `DATABASE_REF` de `state/release.env`, iniciar exclusivamente `cloudflared` usando `deploy/compose.server.yml`, el mismo proyecto y `--profile public --no-deps`. No reiniciar la API ni servicios anteriores para publicar el tunel.
- En esta instalacion se uso `scripts/connect-tunnel.py --tunnel-id bd996162-c96e-487b-8c8e-96a6bc1de4a0 --token-file /home/simonvalt/tickets-demo/state/new-tunnel.token`. Valida que el token corresponda al nuevo ID y rechaza explicitamente el tunel anterior `servidor1`. El archivo de transferencia se elimina despues de conectar; el token permanece solo en `state/runtime.env`.
- Verificacion operativa sin exponer credenciales: `python3 scripts/smoke-server.py` (red interna) o `python3 scripts/smoke-server.py --public` (HTTPS real, validando el certificado). Comprueba login, renovacion, logout, CORS, cookie segura y docs ocultos. No usa `--insecure` ni desactiva TLS.
- Verificar salud publica, login, renovacion y logout; CORS permitido para `https://tickets.sivalt.com` y denegado para otro origen; cookie `HttpOnly; Secure; SameSite=Lax`; docs 404; CSP, `no-store` de rutas/config y cache de bundles. Ejecutar el flujo E2E contra los dominios finales con credenciales privadas (genera registros QA).

## Actualizaciones y recuperacion

Antes de migrar una demo existente se crea `pg_dump -Fc`. Si falla la migracion, se detiene el despliegue. Si la API nueva no queda saludable, se intenta volver a la imagen anterior, sin bajar el esquema. El rollback de imagen solo funciona si la migracion es compatible con la version anterior; para un cambio destructivo se necesita restaurar el respaldo explicitamente. No restaurar ni degradar una BD automaticamente.

El runner compara contenedores ajenos antes y despues de cada ejecucion usando IDs y horas de arranque. El control inicial se conserva aparte para auditar esta instalacion.

## Retirar la prueba tecnica

Primero detener/eliminar **solo** los tres jobs `Tickets-*` y retirar el permiso nuevo `/etc/sudoers.d/tickets-demo`. Detener el proyecto `consultora-tickets-demo` usando su archivo Compose y configuracion privados. `down` conserva el volumen; **no** agregar `--volumes` sin decidir expresamente borrar los datos y conservar un respaldo. Luego retirar exclusivamente los nuevos registros DNS, el tunel `tickets-demo`, la identidad y el proyecto Firebase `sivalt-tickets-demo`. Las carpetas de releases y respaldos no se eliminan automaticamente. Nunca hacer limpieza global de Docker.

Documentacion de referencia: [proyectos Compose](https://docs.docker.com/compose/intro/compose-application-model/), [Jenkins Pipeline](https://www.jenkins.io/doc/book/pipeline/syntax/), [Firebase CLI y ADC](https://firebase.google.com/docs/cli), [dominios de Hosting](https://firebase.google.com/docs/hosting/custom-domain).
