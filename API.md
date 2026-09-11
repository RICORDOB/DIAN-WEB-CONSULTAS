# API — DIAN Web

Documentación técnica de los endpoints. Autenticación por **cookie de sesión**
(`sesion`) firmada con `APP_SECRET_KEY`; se establece tras `POST /api/login`.

**Convención de código de estado:**
- `200` OK · `400` error de validación/argumento · `401` no autenticado ·
  `403` sin permiso · `404` no encontrado · `409` conflicto · `303` redirect.

---

## Vistas (HTML)

| Método | Ruta | Protección | Descripción |
|---|---|---|---|
| GET | `/` | pública | Landing + formulario de registro/login |
| GET | `/panel` | sesión | Consulta individual |
| GET | `/dev` | admin | Panel del desarrollador |
| GET | `/contadores` | admin o `acceso_contador` | Panel de consultas masivas |
| GET | `/privacidad` | pública | Política de privacidad |
| GET | `/manifest.webmanifest` | pública | PWA manifest |
| GET | `/sw.js` | pública | Service Worker (cache) |

Protecciones: `/panel` requiere sesión; `/dev` requiere admin; `/contadores`
requiere admin **o** `acceso_contador` (si no tiene permiso redirige a `/panel`).

---

## Autenticación / cuenta

### POST `/api/registro`
Pública (con rate limit). Cuerpo: `{"usuario": str, "password": str}`.
- Crea la solicitud de alta en estado `pendiente`.
- Respuesta: `{"mensaje", "usuario", "estado": "pendiente"}`.

### POST `/api/login`
Pública (con rate limit). Cuerpo: `{"usuario": str, "password": str}`.
- Verifica credenciales y estado (aprobado y no bloqueado). `401` si falla o está pendiente/bloqueado.
- Establece la cookie de sesión.

### POST `/api/logout`
Sesión. Cierra la sesión actual.

### GET `/api/me`
Sesión. Devuelve el estado actual del usuario:
`{"autenticado": bool, "usuario", "rol": "admin"|"usuario"|"contador",
"acceso_contador": bool|null}`.

### POST `/api/consulta`
Sesión (con rate limit). Cuerpo:
`{"tipo_documento": str, "numero_documento": str, "contrasena": str}`.
- Lanza una **consulta individual** (login DIAN → exógena → renta → FE) en segundo plano.
- Respuesta: `{"job_id"}`. El progreso se consulta vía `GET /api/job/{job_id}`.

### POST `/api/rut`
Sesión (con rate limit). Cuerpo:
`{"tipo_documento": str, "numero_documento": str, "contrasena": str}`.
- Lanza la **obtención de la copia del RUT en PDF** en segundo plano: inicia sesión en
  MUISCA con las mismas credenciales y descarga el certificado desde el dashboard
  (enlace "Obtener copia RUT").
- Respuesta: `{"job_id"}`. La descarga se hace vía `GET /api/job/{job_id}/descargar`.

### GET `/api/job/{job_id}`
Solo el dueño. Devuelve el estado/progreso de una consulta individual:
`{"estado": "queued|running|done|error", "tipo": "xls|rut", "progreso": [str], "final", "error", "resultado"}`.

### GET `/api/job/{job_id}/descargar`
Solo el dueño. Devuelve el libro `.xls` de 3 hojas (consulta ExoRenta) o el **PDF del RUT**
(cuando `tipo == "rut"`); `409` si aún no está listo. El media type lo define la extensión
del archivo (`application/pdf` para `.pdf`, `application/vnd.ms-excel` para `.xls`).

---

## Chat bot (asistente web)

Chat flotante en la landing pública. El cliente escribe **solo su cédula**; el bot busca
sus credenciales DIAN en la tabla `clientes_autorizados` (cifradas) y ofrece la **copia del
RUT (PDF)** o la **Consulta ExoRenta (xls)**. No requiere sesión: el chat se identifica con
una cookie firmada anónima (`bot`, 12 h). Los jobs lanzados se marcan como `bot:<chat_id>`.

| Método | Ruta | Protección | Descripción |
|---|---|---|---|
| POST | `/api/bot/mensaje` | pública (RL 30) | `{"mensaje": str}` → estado del diálogo |
| POST | `/api/bot/accion` | pública (RL 30) | `{"accion": "rut"\|"consulta"\|"manual"\|"reintentar"\|"registro"}` |
| GET | `/api/bot/job/{job_id}` | cookie `bot` dueña | Progreso; en `done` devuelve `descarga` (token) |
| GET | `/api/bot/descargar/{token}` | cookie `bot` dueña | Entrega el archivo; token de **1-uso y 15 min** |

Flujo típico:
1. `POST /api/bot/mensaje {"mensaje": "hola"}` → responde con `cookie` nueva (guardarla).
2. `POST /api/bot/mensaje {"mensaje": "31200506", cookie}` → si el cliente existe, devuelve
   `acciones` con `rut` y `consulta`; si no, acciones `manual`/`reintentar`/`registro`.
3. `POST /api/bot/accion {"accion": "rut", cookie}` → lanza el job y responde `{"job_id"}`.
4. Polling `GET /api/bot/job/{job_id}` (misma cookie) → al terminar:
   `{"estado": "done", "descarga": "/api/bot/descargar/<token>"}`.
5. `GET /api/bot/descargar/<token>` → **una sola vez**; una segunda descarga responde `403`.

Conversaciones sin actividad durante **15 min** se descartan. Las credenciales solo se
descifran en memoria en el instante de lanzar el job y nunca viajan en la respuesta.

### Subida del catálogo (admin)

| Método | Ruta | Protección | Descripción |
|---|---|---|---|
| POST | `/api/admin/clientes` | admin (RL 5) | Multipart `archivo` `.xlsx` (máx. 10 MB) → cifra e importa |
| GET | `/api/admin/clientes` | admin | Lista sin credenciales |
| DELETE | `/api/admin/clientes/{cedula}` | admin | Quita un cliente del catálogo |

`POST /api/admin/clientes` importa **todas** las filas (incluidas las de `estado = ok`),
usa las mismas 3 columnas obligatorias de la plantilla masiva y cifra cada `contrasena` con
Fernet (clave derivada de `APP_SECRET_KEY`) antes de persistir. Respuesta:
`{"cargados": int, "actualizados": int, "total": int}`.

---

## Notificaciones push

| Método | Ruta | Protección | Descripción |
|---|---|---|---|
| GET | `/api/push/clave` | sesión | Publica la VAPID public key para suscribirse |
| POST | `/api/push/registrar` | sesión | Guarda una suscripción push del usuario |
| POST | `/api/push/eliminar` | sesión | Elimina la suscripción del usuario |

La clave VAPID se autogenera y persiste en la tabla `config` si no se provee por variables.

---

## Consultas masivas (panel Contadores)

El rol se controla con `acceso_contador` (activado por el admin). Estados por fila:
`ok`, `error_credenciales`, `desconocido`, `excepcion`.

### GET `/api/masiva/plantilla`
Admin o `acceso_contador`. Descarga `plantilla_clientes.xlsx` con columnas:
`tipo_documento | numero_documento | contrasena | fecha_vencimiento | estado`.
La primera hoja es la activa (`clientes`); solo encabezados, **sin fila de ejemplo**.

### POST `/api/masiva/upload`
Admin o `acceso_contador`. Multipart con campo `archivo` (`.xlsx`, máx. 10 MB).
- Valida columnas requeridas (`tipo_documento`, `numero_documento`, `contrasena`) → `400` si faltan.
- Lanza el procesamiento en segundo plano.
- Respuesta: `{"batch_id", "total"}`.

### GET `/api/masiva/{batch_id}`
Solo el dueño (o admin). Estado del procesamiento:
`{"estado": "queued|running|done|error", "total", "done", "progreso": [str],
"resumen", "error", "detalle": [{fila_excel, numero_documento, final|error}]}`.

### GET `/api/masiva/{batch_id}/descargar`
Solo el dueño (o admin), `409` si aún no termina. Devuelve un **ZIP** con:
- `resultado_masiva.xlsx` (el Excel de entrada con `estado` y `fecha_vencimiento` alimentadas).
- `clientes/*.xls`: un archivo por cliente procesado correctamente.

---

## Administración

| Método | Ruta | Cuerpo | Descripción |
|---|---|---|---|
| GET | `/api/admin/pendientes` | — | Solicitudes de alta pendientes |
| POST | `/api/admin/decidir` | `{"usuario", "aprobar": bool}` | Aprueba/rechaza un alta |
| POST | `/api/admin/bloquear` | `{"usuario", "bloquear": bool}` | Bloquea/desbloquea (revoca sesión en vivo) |
| POST | `/api/admin/eliminar` | `{"usuario"}` | Elimina cuenta + historial |
| POST | `/api/admin/contador` | `{"usuario", "activar": bool}` | Activa/desactiva acceso masivo de pago |
| GET | `/api/admin/estadisticas` | — | KPIs/dashboard |
| GET | `/api/admin/consultas` | query: `usuario`, `estado`, `limite` (≤200) | Historial (sin n.º de documento) |

Reglas del admin:
- No puede modificar/bloquear/eliminar su **propia** cuenta.
- No puede eliminar una cuenta de administrador.
- `decidir` devuelve `400` si el usuario es el propio admin.

---

## Motor de consultas masivas (`app/batch.py`)

- `cargar_filas(ruta)` → `(filas, encabezados)`: lee la primera hoja, normaliza
  encabezados, filtra filas vacías y las de `estado = ok`, exige las 3 columnas requeridas.
- `generar_plantilla(ruta/bytesio)` → crea el `.xlsx` con las 5 columnas (sin fila datos).
- `ejecutar_batch(job_dir, entrada, progreso)` → procesa cada fila con
  `DianRunner.consulta_individual` y **alimenta** `estado` y `fecha_vencimiento`.
  Lanza excepción si el `.xlsx` es inválido.
- Los batches viven **en memoria** (`_batches` en `main.py`) con un `_job_lock` para
  serializar escrituras; los resultados se limpian tras 1 hora.

## Persistencia

- `app/db.py`: dos backends intercambiables — **Turso (libSQL, HTTP v2)** cuando existen
  `TURSO_DB_URL` + `TURSO_AUTH_TOKEN`, **SQLite local** como fallback.
- Tablas (creadas en `auth.iniciar_db()`):
  - `usuarios` — cuenta, `rol` (`usuario`|`admin`|`contador`), `estado`, y columna
    `acceso_contador` (int, migrada automáticamente si no existe).
  - `consultas` — historial de consultas individuales (id, usuario, tipo, fechas,
    estado, resultado, error), **sin número de documento**.
  - `registros` — bitácora de acciones administrativas.
  - `push_suscripciones` — suscripciones push por usuario.
  - `config` — pares clave/valor (p. ej. la clave VAPID generada).
  - `clientes_autorizados` — catálogo del chat bot: `numero_documento` (PK),
    `tipo_documento`, `contrasena_cifrada`, `fecha_vencimiento`,
    `contrasena_propia`, `creado_en`.
- Las credenciales de clientes de la DIAN **no se guardan en texto plano**: la contraseña
  de la cuenta web se guarda solo como hash PBKDF2 y las credenciales del bot se cifran con
  Fernet antes de persistir (nunca se exponen vía API; cambiar `APP_SECRET_KEY` las invalida).
