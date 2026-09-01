# DIAN Web — Consulta de información exógena por navegador

Aplicación web (FastAPI + Playwright) que replica la automatización original de
consulta a la DIAN pero accesible desde **cualquier dispositivo por un navegador**,
sin instalar nada en el equipo del usuario.

> **Estado:** consultas **individuales** y **masivas** (panel Contadores con acceso
> de pago activable por el administrador), notificaciones push, política de
> privacidad y despliegue continuo en Render.

## Flujo

1. El usuario **se registra** (`/`) → su solicitud de alta queda **pendiente**.
2. El **desarrollador/administrador** aprueba el alta desde su panel (`/dev`).
3. Con la cuenta aprobada, el usuario inicia sesión y entra a `/panel`.
4. En el panel escribe **tipo de documento, número de cédula y contraseña** y pulsa
   **CONSULTAR**.
5. En el servidor se ejecuta internamente el proceso (login DIAN → exógena →
   análisis de renta → facturación electrónica), igual que al usar el script original.
6. Al terminar, se habilita el botón **DESCARGAR RESULTADO**: el `.xls` de 3 hojas
   (Información Exógena / Renta / Facturación Electrónica), idéntico al del script original.

### Consultas masivas (panel Contadores)

- El administrador activa el acceso de pago a un usuario desde `/dev`
  (botón **Activar Contador**). Solo quienes tengan `acceso_contador` entran a `/contadores`.
- El contador sube un **.xlsx** (una fila = un cliente) y el sistema procesa todos
  **secuencialmente**, reutilizando la consulta individual por fila.
- Botón **DESCARGAR PLANTILLA** (`/api/masiva/plantilla`) con las columnas exactas.
- Al terminar, el sistema **alimenta** en el archivo:
  - `estado`: resultado por fila (`ok`, `error_credenciales`, `desconocido`, `excepcion`).
  - `fecha_vencimiento`: fecha calculada del calendario DIAN según los dígitos del documento.
- Las filas con `estado = ok` se **saltan** en re-ejecuciones (proceso resumible).
- Descarga de resultados en **ZIP**: `resultado_masiva.xlsx` (con estados) + los
  `.xls` de cada cliente.

## Estructura

```
app/
  main.py        # API FastAPI + vistas y gestión de jobs/batches
  runner.py      # núcleo reutilizado (DianRunner): login, descargas, análisis, armado
  batch.py       # motor de consultas masivas: plantilla, cargar filas, ejecutar, estados
  auth.py        # usuarios, alta por aprobación, bloqueos, roles, sesiones firmadas
  db.py          # persistencia con dos backends: Turso (libSQL) y SQLite local
  push.py        # notificaciones push (VAPID + suscripciones)
  comun.py       # constantes compartidas (script y web): UVT, topes, selectores, calendario
  static/        # index, panel, dev, contadores, privacidad, styles, app.js, sw.js, manifest
tests/           # pytest: auth, batch, api, push, db y análisis de renta
requirements-dev.txt  # dependencias para desarrollo/pruebas (pytest)
.env.example     # plantilla de variables de entorno
Dockerfile
requirements.txt
```

## Roles y acceso

- **admin** (`APP_ADMIN_USER`): aprueba/rechaza altas, bloquea/desbloquea, activa el
  acceso Contador y administra el panel `/dev`.
- **usuario común**: acceso solo tras aprobación; puede usar el panel `/panel`.
- **contador**: usuario al que el admin le activó `acceso_contador`; entra a `/contadores`.

## Panel del desarrollador

Además de aprobar/rechazar solicitudes de alta, el panel `/dev` permite:

- **Bloquear/desbloquear** el acceso de cualquier usuario; el bloqueo **revoca la sesión
  en la siguiente petición** (se valida el estado en BD en cada llamada).
- **Activar/desactivar Contador** por usuario (habilitar consultas masivas de pago).
- **Dashboard interactivo** (Chart.js vía CDN): KPIs de usuarios y consultas, consultas
  por día (14 días) y por estado, y el historial de consultas por usuario.
- Los datos sensibles de clientes (número de documento) **no se persisten**: el historial
  guarda solo usuario, tipo de documento, fechas y estado/resultado.

## Configuración por variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `APP_ADMIN_USER` | Usuario del primer administrador (quien aprueba altas) | `desarrollador` |
| `APP_ADMIN_PASS` | Contraseña del administrador | `cambiar-me` |
| `APP_SECRET_KEY` | Clave para firmar sesiones. **Obligatoria** (la app no arranca sin ella, salvo en dev) | `$(openssl rand -hex 32)` |
| `APP_ENV` | `dev` desactiva el fail-fast de `APP_SECRET_KEY` para desarrollo local | `dev` |
| `APP_SESSION_HOURS` | Duración de sesión en horas (default 12) | `12` |
| `APP_DATA_DIR` | Carpeta de persistencia local (DB) | `data` |
| `APP_JOBS_DIR` | Carpeta de trabajos (descargas temporales) | `data/jobs` |
| `TURSO_DB_URL` | URL de la base Turso (libSQL). Si está set con `TURSO_AUTH_TOKEN`, la BD es en la nube | `libsql://...` |
| `TURSO_AUTH_TOKEN` | Token de acceso a Turso | `...` |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Claves para notificaciones push (se generan y persisten en `config` si faltan) | `...` |

El administrador se crea automáticamente al primer arranque si las variables
`APP_ADMIN_USER`/`APP_ADMIN_PASS` están definidas y no existe aún.

**Persistencia:** la app usa **Turso (libSQL)** cuando hay credenciales `TURSO_*`; de lo
contrario cae a **SQLite local**. `db.py` expone una interfaz única (cursor con
`fetchone/fetchall/rowcount`, filas como dict) para cualquiera de los dos backends.
Las notificaciones push persisten su `VAPID` en la tabla `config`.

## Ejecución local

Con la imagen Playwright ya instalada (ver Dockerfile), o localmente:

```bash
# dependencias
pip install -r requirements.txt
playwright install chromium

# arrancar
export APP_ADMIN_USER=admin APP_ADMIN_PASS=admin123 APP_SECRET_KEY=abc
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Para desarrollo local sin `APP_SECRET_KEY`: `export APP_ENV=dev`.

Abrir `http://localhost:8000`.

### Con Docker (recomendado)

```bash
docker build -t dian-web .
docker run --rm -p 8000:8000 \
  -e APP_ADMIN_USER=admin -e APP_ADMIN_PASS=admin123 -e APP_SECRET_KEY=abc \
  dian-web
```

## Despliegue en Render

Hay un pipeline de despliegue continuo (`.github/workflows/deploy.yml`) que publica en
Render al hacer push a `main`. Guía manual detallada en `DEPLOY_RENDER.md`.

1. Sube este proyecto a un repositorio de GitHub.
2. En Render crea un **Web Service** apuntando a ese repo.
3. Render auto-detecta el `Dockerfile`.
4. Define las variables de entorno `APP_ADMIN_USER`, `APP_ADMIN_PASS`, `APP_SECRET_KEY`
   y (en producción) `TURSO_DB_URL`/`TURSO_AUTH_TOKEN`.
5. Despliega. Ajusta la instancia a Standard / más RAM si Chromium falla con lotes o
   consultas grandes.

## Seguridad

- Las credenciales de clientes existen **solo en memoria** durante la tarea y se
  descartan al terminar (los directorios temporales se limpian tras 1h).
- Las contraseñas de usuarios se almacenan con hash (PBKDF2 + salt).
- Las sesiones se firman con `APP_SECRET_KEY`; la app **no arranca** sin ella
  (fail-fast) salvo en desarrollo con `APP_ENV=dev`.
- Bloquear a un usuario lo revoca al instante: cada petición valida su estado en BD.
- Headers de seguridad (X-Frame-Options, X-Content-Type-Options, Referrer-Policy) en
  todas las respuestas.
- Rate limiting por IP en `/api/login`, `/api/registro` y `/api/consulta`.
- HTTPS lo provee la plataforma.
- **Nunca** se deben incluir `clientes_dian.xlsx`, `cliente_individual.xlsx` ni
  `credentials.json` en el repositorio (están en `.gitignore`).
- Política de privacidad pública en `/privacidad`.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest -q
```

## API

La lista completa de endpoints, métodos y protección está en **`API.md`**.

## Documentación

| Documento | Contenido |
|---|---|
| `README.md` | Este archivo: visión, estructura, configuración, uso |
| `API.md` | Todos los endpoints (método, protección, cuerpo, respuesta) |
| `GUIA_USUARIO.md` | Guía de uso (no técnica) para contadores y usuarios finales |
| `DEPLOY_RENDER.md` | Despliegue: CI automático, variables, backup, solución de problemas |
| `RESUMEN_PROYECTO_WEB.txt` | Resumen ejecutivo del proyecto web actual |
| `RESUMEN_SCRIPT_ORIGINAL.txt` | (histórico) resumen del script local `dian_login.py` |

## Notas técnicas (gotchas conocidos)

- La **versión de `playwright` en `requirements.txt` debe coincidir** con la etiqueta
  de la imagen base `mcr.microsoft.com/playwright/python:v...` del `Dockerfile`, o el
  navegador no se encontrará.
- Lanzamiento de Chromium en contenedores de poca RAM: `--no-sandbox`,
  `--disable-dev-shm-usage`, `--single-process` están configurados en `runner.py`.
- Los reportes de la DIAN llegan con extensión `.xls` pero contenido XLSX; el runner
  los copia a `.xlsx` temporal para abrirlos con `openpyxl`, igual que el original.
- `ejecutar_batch` es async: al lanzarlo desde `main.py` se envuelve en
  `asyncio.run()` dentro de un hilo (`asyncio.to_thread`) para obtener el dict real
  (no una corrutina) sin bloquear el event loop.
