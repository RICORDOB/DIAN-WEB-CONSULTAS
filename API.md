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

### GET `/api/job/{job_id}`
Solo el dueño. Devuelve el estado/progreso de una consulta individual:
`{"estado": "queued|running|done|error", "progreso": [str], "final", "error", "resultado"}`.

### GET `/api/job/{job_id}/descargar`
Solo el dueño. Devuelve el `.xls` de 3 hojas generado (`FileResponse`, `409` si aún no está listo).

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
- Las credenciales de clientes de la DIAN **nunca se persisten**; solo el hash PBKDF2
  de la contraseña de la cuenta del usuario y el registro de la consulta (sin documento).
