"""
auth — Gestión de usuarios, alta con aprobación del desarrollador y sesiones.

Flujo de alta (solo la primera vez):
  1. Un usuario se registra -> queda en estado 'pendiente'.
  2. La solicitud aparece en el panel del desarrollador (admin).
  3. El admin aprueba (o rechaza) -> el usuario ya puede iniciar sesión.
  4. En adelante el login corre con normalidad para ese usuario.

El primer administrador se crea desde variables de entorno (APP_ADMIN_USER /
APP_ADMIN_PASS), para no hardcodear credenciales en el repositorio.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import date, timedelta
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import db as _db

# Roles de un usuario
ROL_USUARIO = "usuario"
ROL_CONTADOR = "contador"
ROL_ADMIN = "admin"

# Estados de un usuario
PENDIENTE = "pendiente"
APROBADO = "aprobado"
RECHAZADO = "rechazado"
BLOQUEADO = "bloqueado"

# Estados de una consulta (reflejan el estado del job)
ESTADO_QUEUED = "queued"
ESTADO_RUNNING = "running"
ESTADO_DONE = "done"
ESTADO_ERROR = "error"

# Origen de la clave DIAN guardada en el catálogo de clientes.
ORIGEN_MANUAL = "manual"          # cargada por el admin (xlsx)
ORIGEN_RECUPERADA = "recuperada"  # restablecida por el flujo de recuperación
ORIGEN_HABILITADA = "habilitada"  # creada con el flujo «Usuario Nuevo»
_ORIGENES_VALIDOS = {ORIGEN_MANUAL, ORIGEN_RECUPERADA, ORIGEN_HABILITADA}

# Rutas de almacenamiento (configurables por variables de entorno en despliegue)
_data_dir = Path(os.environ.get("APP_DATA_DIR", "data"))
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "usuarios.db"


class AuthError(Exception):
    """Error controlado de autenticación con mensaje para el usuario."""


# ---------------------------------------------------------------------------
# Contraseñas (hash seguro con salt)
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Devuelve (hash, salt). Hash = pbkdf2 sha256 con 100k iteraciones."""
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000
    ).hex()
    return digest, salt


def _verificar_password(password: str, salt: str, hash_esperado: str) -> bool:
    """Compara la contraseña contra el hash almacenado (comparación en tiempo constante)."""
    digest, _ = _hash_password(password, salt)
    return hmac.compare_digest(digest, hash_esperado)


# ---------------------------------------------------------------------------
# Sesión (cookie firmada)
# ---------------------------------------------------------------------------
_secret = os.environ.get("APP_SECRET_KEY", "")
if not _secret and os.environ.get("APP_ENV") != "dev":
    raise RuntimeError(
        "APP_SECRET_KEY no está definida. Genera una con `openssl rand -hex 32` "
        "y defínela en el entorno (o ejecuta con APP_ENV=dev para desarrollo local)."
    )
_sessions = URLSafeTimedSerializer(_secret or "clave-dev-no-usar")
SESSION_MAX_AGE = int(os.environ.get("APP_SESSION_HOURS", 12)) * 3600


def crear_token(usuario: str, rol: str) -> str:
    """Genera un token firmado con el usuario y su rol."""
    return _sessions.dumps({"usuario": usuario, "rol": rol})


def leer_token(token: str) -> dict | None:
    """Valida y decodifica un token; devuelve None si es inválido o caducó."""
    try:
        return _sessions.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def _conectar():
    """Conexión al backend activo: Turso si hay credenciales, si no sqlite3 local."""
    return _db.conectar()


_SCHEMA_CLIENTES = """
    CREATE TABLE IF NOT EXISTS clientes_autorizados (
        numero_documento TEXT PRIMARY KEY,
        tipo_documento TEXT NOT NULL DEFAULT 'Cédula de Ciudadanía',
        contrasena_cifrada TEXT,
        fecha_vencimiento TEXT,
        telefono TEXT,
        contrasena_propia INTEGER NOT NULL DEFAULT 1,
        origen_clave TEXT,
        fecha_expedicion_doc TEXT,
        creado_en TEXT NOT NULL
    )
"""


def _migrar_clientes_clave_opcional(conn) -> None:
    """Permite clientes sin clave y agrega el origen y la fecha de expedición.

    SQLite y Turso no eliminan ``NOT NULL`` con ``ALTER TABLE``: si la tabla
    vieja todavía exige ``contrasena_cifrada`` hay que reconstruirla. Todo
    ocurre dentro de la transacción de ``iniciar_db`` (atómico y repetible).
    """
    info = conn.execute("PRAGMA table_info(clientes_autorizados)").fetchall()
    columnas = {r["name"] for r in info}
    clave_obligatoria = False
    for r in info:
        if r["name"] != "contrasena_cifrada":
            continue
        try:
            clave_obligatoria = bool(r["notnull"])
        except (KeyError, IndexError, TypeError, ValueError):
            clave_obligatoria = False

    if not clave_obligatoria:
        if "origen_clave" not in columnas:
            conn.execute(
                "ALTER TABLE clientes_autorizados ADD COLUMN origen_clave TEXT"
            )
        if "fecha_expedicion_doc" not in columnas:
            conn.execute(
                "ALTER TABLE clientes_autorizados ADD COLUMN fecha_expedicion_doc TEXT"
            )
        return

    # Filas existentes: las cargadas por el admin (propia=1) son manuales; las
    # aportadas por el propio cliente, recuperadas.
    origen_select = (
        "origen_clave"
        if "origen_clave" in columnas
        else "CASE WHEN contrasena_cifrada IS NULL "
        "OR TRIM(contrasena_cifrada) = '' THEN NULL "
        "WHEN COALESCE(contrasena_propia, 1) = 1 THEN 'manual' "
        "ELSE 'recuperada' END"
    )
    fecha_select = (
        "fecha_expedicion_doc" if "fecha_expedicion_doc" in columnas else "NULL"
    )
    conn.execute("DROP TABLE IF EXISTS clientes_autorizados_previo")
    conn.execute(
        "ALTER TABLE clientes_autorizados RENAME TO clientes_autorizados_previo"
    )
    conn.execute(_SCHEMA_CLIENTES)
    conn.execute(
        f"""
        INSERT INTO clientes_autorizados
            (numero_documento, tipo_documento, contrasena_cifrada,
             fecha_vencimiento, telefono, contrasena_propia, origen_clave,
             fecha_expedicion_doc, creado_en)
        SELECT numero_documento, tipo_documento, contrasena_cifrada,
               fecha_vencimiento, telefono, COALESCE(contrasena_propia, 1),
               {origen_select}, {fecha_select}, creado_en
        FROM clientes_autorizados_previo
        """
    )
    conn.execute("DROP TABLE clientes_autorizados_previo")


def iniciar_db() -> None:
    """Crea las tablas y siembra el primer admin desde variables de entorno."""
    with _conectar() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT UNIQUE NOT NULL,
                hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'usuario',
                estado TEXT NOT NULL DEFAULT 'pendiente',
                creado_en TEXT NOT NULL
            )
            """
        )
        # Migración: columna de acceso de pago a consultas masivas (contador).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()}
        if "acceso_contador" not in cols:
            conn.execute(
                "ALTER TABLE usuarios ADD COLUMN acceso_contador INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                accion TEXT NOT NULL,
                quien TEXT NOT NULL,
                usuario TEXT NOT NULL,
                cuando TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consultas (
                id TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                tipo_documento TEXT,
                fecha_creacion TEXT NOT NULL,
                fecha_fin TEXT,
                estado TEXT NOT NULL,
                resultado TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_consultas_fecha ON consultas(fecha_creacion)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_suscripciones (
                usuario TEXT NOT NULL,
                endpoint TEXT PRIMARY KEY,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                creado_en TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_consultas_estado ON consultas(estado)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
            """
        )
        # Clientes autorizados para el chat bot: credenciales DIAN cifradas.
        # numero_documento es la PK; contrasena_cifrada guarda la contraseña con
        # Fernet (app/bot/cifrado.py). Puede ser NULL: cliente sin clave, que se
        # habilita con el flujo «Usuario Nuevo». contrasena_propia=1 -> credencial
        # cargada por el admin desde clientes_dian.xlsx; contrasena_propia=0 -> la
        # aportó el propio cliente (recuperación o habilitación).
        conn.execute(_SCHEMA_CLIENTES)
        # Migración: teléfono de contacto del cliente (columna del xlsx; se usa
        # para los avisos de vencimiento y un futuro canal de mensajería).
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(clientes_autorizados)").fetchall()}
        if "telefono" not in cols:
            conn.execute("ALTER TABLE clientes_autorizados ADD COLUMN telefono TEXT")
        # Migración: clave opcional + origen + fecha de expedición del documento.
        _migrar_clientes_clave_opcional(conn)
        admin_user = os.environ.get("APP_ADMIN_USER")
        admin_pass = os.environ.get("APP_ADMIN_PASS")
        if admin_user and admin_pass:
            fila = conn.execute(
                "SELECT hash, salt, rol, estado FROM usuarios WHERE usuario = ?",
                (admin_user,),
            ).fetchone()
            if fila is None:
                h, s = _hash_password(admin_pass)
                conn.execute(
                    "INSERT INTO usuarios (usuario, hash, salt, rol, estado, creado_en) "
                    "VALUES (?, ?, ?, 'admin', 'aprobado', ?)",
                    (admin_user, h, s, time.strftime("%Y-%m-%d %H:%M:%S")),
                )
                print(f"[admin] administrador '{admin_user}' creado desde APP_ADMIN_USER")
            elif (
                not _verificar_password(admin_pass, fila["salt"], fila["hash"])
                or fila["rol"] != ROL_ADMIN
                or fila["estado"] != APROBADO
            ):
                h, s = _hash_password(admin_pass)
                conn.execute(
                    "UPDATE usuarios SET hash = ?, salt = ?, rol = 'admin', estado = ? "
                    "WHERE usuario = ?",
                    (h, s, APROBADO, admin_user),
                )
                print(f"[admin] administrador '{admin_user}' actualizado desde APP_ADMIN_PASS")
            else:
                print(f"[admin] administrador '{admin_user}' al día")


# ---------------------------------------------------------------------------
# Claves de configuración persistente (p. ej. llave VAPID para push)
# ---------------------------------------------------------------------------
def config_get(clave: str) -> str | None:
    with _conectar() as conn:
        row = conn.execute(
            "SELECT valor FROM config WHERE clave = ?", (clave,)
        ).fetchone()
    return row["valor"] if row else None


def config_set(clave: str, valor: str) -> None:
    with _conectar() as conn:
        conn.execute(
            "INSERT INTO config (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )


# ---------------------------------------------------------------------------
# Operaciones de usuario
# ---------------------------------------------------------------------------
def registrar(usuario: str, password: str) -> dict:
    """Crea una solicitud de alta. Devuelve un dict descriptivo."""
    usuario = usuario.strip().lower()
    if not usuario or len(usuario) < 3:
        raise AuthError("El usuario debe tener al menos 3 caracteres.")
    if len(password) < 6:
        raise AuthError("La contraseña debe tener al menos 6 caracteres.")
    if usuario == os.environ.get("APP_ADMIN_USER", "").strip().lower():
        raise AuthError("Ese nombre de usuario no está disponible.")

    with _conectar() as conn:
        existe = conn.execute(
            "SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
        if existe:
            raise AuthError("El usuario ya existe.")
        h, s = _hash_password(password)
        conn.execute(
            "INSERT INTO usuarios (usuario, hash, salt, rol, estado, creado_en) "
            "VALUES (?, ?, ?, 'usuario', 'pendiente', ?)",
            (usuario, h, s, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {"usuario": usuario, "estado": PENDIENTE}


def verificar_login(usuario: str, password: str) -> dict:
    """Valida credenciales de un usuario ya aprobado. Lanza AuthError con motivo."""
    usuario = usuario.strip().lower()
    with _conectar() as conn:
        row = conn.execute(
            "SELECT * FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
    if row is None:
        raise AuthError("Usuario o contraseña incorrectos.")
    if not _verificar_password(password, row["salt"], row["hash"]):
        raise AuthError("Usuario o contraseña incorrectos.")
    if row["estado"] == PENDIENTE:
        raise AuthError("Tu cuenta está pendiente de aprobación por el administrador.")
    if row["estado"] == RECHAZADO:
        raise AuthError("Tu solicitud de alta fue rechazada.")
    if row["estado"] == BLOQUEADO:
        raise AuthError("Tu cuenta está bloqueada. Contacta al administrador.")
    if row["estado"] != APROBADO:
        raise AuthError("Tu cuenta no está habilitada para iniciar sesión.")
    return {"usuario": row["usuario"], "rol": row["rol"], "estado": row["estado"]}


def estado_usuario(usuario: str) -> str | None:
    """Devuelve el estado actual de un usuario, o None si no existe."""
    with _conectar() as conn:
        row = conn.execute(
            "SELECT estado FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
    return row["estado"] if row else None


def bloquear_usuario(usuario: str, bloquear: bool, admin: str) -> dict:
    """Bloquea o desbloquea el acceso de un usuario. Registra la acción."""
    nuevo_estado = BLOQUEADO if bloquear else APROBADO
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET estado = ? WHERE usuario = ?",
            (nuevo_estado, usuario),
        )
        if cur.rowcount == 0:
            raise AuthError(f"No se encontró el usuario '{usuario}'.")
        conn.execute(
            "INSERT INTO registros (accion, quien, usuario, cuando) "
            "VALUES (?, ?, ?, ?)",
            ("bloquear" if bloquear else "desbloquear", admin, usuario,
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {"usuario": usuario, "estado": nuevo_estado}


# ---------------------------------------------------------------------------
# Panel de desarrollador
# ---------------------------------------------------------------------------
def listar_pendientes() -> list[dict]:
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT id, usuario, rol, estado, creado_en, acceso_contador "
            "FROM usuarios ORDER BY creado_en DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def decidir_alta(usuario: str, aprobar: bool, admin: str) -> dict:
    """Aprueba o rechaza la solicitud de alta de un usuario. Solo admin."""
    nuevo_estado = APROBADO if aprobar else RECHAZADO
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET estado = ? WHERE usuario = ?",
            (nuevo_estado, usuario),
        )
        if cur.rowcount == 0:
            raise AuthError(f"No se encontró la solicitud de '{usuario}'.")
        conn.execute(
            "INSERT INTO registros (accion, quien, usuario, cuando) "
            "VALUES (?, ?, ?, ?)",
            ("aprobar" if aprobar else "rechazar", admin, usuario,
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {"usuario": usuario, "estado": nuevo_estado}


def eliminar_usuario(usuario: str, admin: str) -> dict:
    """Elimina una cuenta de usuario y su historial de consultas. Solo admin.

    Al borrar el registro, `estado_usuario()` devuelve None y la sesión del
    usuario queda revocada en vivo en la siguiente petición.
    """
    with _conectar() as conn:
        cur = conn.execute(
            "DELETE FROM usuarios WHERE usuario = ?", (usuario,)
        )
        if cur.rowcount == 0:
            raise AuthError(f"No se encontró el usuario '{usuario}'.")
        conn.execute("DELETE FROM consultas WHERE usuario = ?", (usuario,))
        conn.execute(
            "INSERT INTO registros (accion, quien, usuario, cuando) "
            "VALUES (?, ?, ?, ?)",
            ("eliminar", admin, usuario, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {"usuario": usuario, "eliminado": True}


def es_admin(usuario: str) -> bool:
    with _conectar() as conn:
        row = conn.execute(
            "SELECT rol FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
    return bool(row and row["rol"] == "admin")


def tiene_acceso_contador(usuario: str) -> bool:
    """Un usuario tiene acceso a consultas masivas si es admin o si el admin le
    activó el acceso de pago (columna acceso_contador)."""
    with _conectar() as conn:
        row = conn.execute(
            "SELECT rol, acceso_contador FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
    if not row:
        return False
    return row["rol"] == ROL_ADMIN or bool(row["acceso_contador"])


def set_acceso_contador(usuario: str, activar: bool, admin: str) -> dict:
    """Activa o desactiva el acceso de pago a consultas masivas de un usuario."""
    nuevo = 1 if activar else 0
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET acceso_contador = ? WHERE usuario = ?",
            (nuevo, usuario),
        )
        if cur.rowcount == 0:
            raise AuthError(f"No se encontró el usuario '{usuario}'.")
        conn.execute(
            "INSERT INTO registros (accion, quien, usuario, cuando) "
            "VALUES (?, ?, ?, ?)",
            ("activar_contador" if activar else "desactivar_contador", admin,
             usuario, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {"usuario": usuario, "acceso_contador": bool(nuevo)}


# ---------------------------------------------------------------------------
# Suscripciones push (PWA)
# ---------------------------------------------------------------------------
def guardar_suscripcion(usuario: str, endpoint: str, p256dh: str, auth_k: str) -> None:
    """Guarda (o actualiza) la suscripción push de un usuario."""
    with _conectar() as conn:
        conn.execute(
            "INSERT INTO push_suscripciones (usuario, endpoint, p256dh, auth, creado_en) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET "
            "usuario = excluded.usuario, p256dh = excluded.p256dh, auth = excluded.auth, "
            "creado_en = excluded.creado_en",
            (usuario, endpoint, p256dh, auth_k, time.strftime("%Y-%m-%d %H:%M:%S")),
        )


def eliminar_suscripcion(endpoint: str) -> None:
    """Elimina una suscripción push (baja voluntaria o endpoint inválido)."""
    with _conectar() as conn:
        conn.execute("DELETE FROM push_suscripciones WHERE endpoint = ?", (endpoint,))


def listar_suscripciones(usuario: str) -> list[dict]:
    """Suscripciones push activas de un usuario."""
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT usuario, endpoint, p256dh, auth FROM push_suscripciones "
            "WHERE usuario = ?",
            (usuario,),
        ).fetchall()
    return [dict(r) for r in rows]


def listar_usuarios_suscritos() -> list[str]:
    """Usuarios con al menos una suscripción push activa."""
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT DISTINCT usuario FROM push_suscripciones"
        ).fetchall()
    return [r["usuario"] for r in rows]


# ---------------------------------------------------------------------------
# Consultas (historial para el dashboard del desarrollador)
# ---------------------------------------------------------------------------
def registrar_consulta(job_id: str, usuario: str, tipo_documento: str) -> None:
    """Persiste el arranque de una consulta (estado 'queued')."""
    with _conectar() as conn:
        conn.execute(
            "INSERT INTO consultas (id, usuario, tipo_documento, fecha_creacion, estado) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, usuario, tipo_documento,
             time.strftime("%Y-%m-%d %H:%M:%S"), ESTADO_QUEUED),
        )


def actualizar_consulta(job_id: str, estado: str, resultado: str | None = None,
                        error: str | None = None) -> None:
    """Actualiza el estado/resultado/error de una consulta al terminar."""
    with _conectar() as conn:
        conn.execute(
            "UPDATE consultas SET estado = ?, resultado = ?, error = ?, "
            "fecha_fin = COALESCE(fecha_fin, ?) WHERE id = ?",
            (estado, resultado, error,
             time.strftime("%Y-%m-%d %H:%M:%S"), job_id),
        )


def marcar_consultas_huerfanas() -> int:
    """Marca como 'error' las consultas que quedaron queued/running (reinicie)."""
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE consultas SET estado = ?, error = ?, fecha_fin = ? "
            "WHERE estado IN (?, ?)",
            (ESTADO_ERROR, "proceso interrumpido (reinicio del servidor)",
             time.strftime("%Y-%m-%d %H:%M:%S"),
             ESTADO_QUEUED, ESTADO_RUNNING),
        )
    return cur.rowcount


def listar_consultas(usuario: str | None = None, estado: str | None = None,
                     limite: int = 100) -> list[dict]:
    """Lista consultas recientes, filtrando opcionalmente por usuario y estado."""
    sql = "SELECT * FROM consultas WHERE 1=1"
    params: list = []
    if usuario:
        sql += " AND usuario = ?"
        params.append(usuario)
    if estado:
        sql += " AND estado = ?"
        params.append(estado)
    sql += " ORDER BY fecha_creacion DESC LIMIT ?"
    params.append(limite)
    with _conectar() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def estadisticas_completas() -> dict:
    """Métricas de usuarios y consultas para el dashboard del desarrollador."""
    with _conectar() as conn:
        usuarios = dict(conn.execute(
            "SELECT estado, COUNT(*) FROM usuarios GROUP BY estado"
        ).fetchall())
        total_c = int(conn.execute(
            "SELECT COUNT(*) FROM consultas"
        ).fetchone()[0])
        por_estado = dict(conn.execute(
            "SELECT estado, COUNT(*) FROM consultas GROUP BY estado"
        ).fetchall())
        rows_ultimas = conn.execute(
            "SELECT fecha_creacion, estado FROM consultas "
            "ORDER BY fecha_creacion DESC LIMIT 1000"
        ).fetchall()
        por_usuario = dict(conn.execute(
            "SELECT usuario, COUNT(*) FROM consultas GROUP BY usuario ORDER BY 2 DESC"
        ).fetchall())

    # Últimos 14 días con ceros para los días sin consultas
    desde = date.today() - timedelta(days=13)
    agrupadas: dict[str, list[int]] = {}
    for x in rows_ultimas:
        dia = (x["fecha_creacion"] or "")[:10]
        if dia < desde.isoformat():
            continue
        agrupadas.setdefault(dia, [0, 0, 0])
        agrupadas[dia][0] += 1
        if x["estado"] == ESTADO_DONE:
            agrupadas[dia][1] += 1
        elif x["estado"] == ESTADO_ERROR:
            agrupadas[dia][2] += 1

    por_dia = []
    for i in range(14):
        dia = (desde + timedelta(days=i)).isoformat()
        a = agrupadas.get(dia, [0, 0, 0])
        por_dia.append({"fecha": dia, "total": a[0], "done": a[1], "error": a[2]})

    return {
        "usuarios": {
            "total": sum(usuarios.values()),
            "aprobados": usuarios.get(APROBADO, 0),
            "pendientes": usuarios.get(PENDIENTE, 0),
            "rechazados": usuarios.get(RECHAZADO, 0),
            "bloqueados": usuarios.get(BLOQUEADO, 0),
        },
        "consultas": {
            "total": total_c,
            "done": por_estado.get(ESTADO_DONE, 0),
            "error": por_estado.get(ESTADO_ERROR, 0),
            "running": por_estado.get(ESTADO_RUNNING, 0),
            "queued": por_estado.get(ESTADO_QUEUED, 0),
        },
        "por_dia": por_dia,
        "por_usuario": [{"usuario": u, "total": t} for u, t in por_usuario.items()],
    }


# ---------------------------------------------------------------------------
# Clientes autorizados (chat bot)
# ---------------------------------------------------------------------------
def guardar_cliente(numero_documento: str, tipo_documento: str,
                    contrasena_cifrada: str | None, fecha_vencimiento: str | None,
                    contrasena_propia: bool = True,
                    telefono: str | None = None,
                    origen_clave: str | None = None,
                    fecha_expedicion_doc: str | None = None) -> dict:
    """Upsert de un cliente autorizado. Devuelve si era nuevo o se actualizó.

    ``contrasena_cifrada=None`` registra un cliente sin clave (para habilitarla
    después). Al re-subir el catálogo sin clave se conserva la existente
    (COALESCE), igual que el teléfono, para no borrar una clave ya habilitada.
    """
    numero = str(numero_documento).strip()
    tel = str(telefono).strip() if telefono not in (None, "") else None
    clave = (
        str(contrasena_cifrada).strip()
        if contrasena_cifrada not in (None, "")
        else None
    )
    if origen_clave not in _ORIGENES_VALIDOS:
        origen_clave = None
    if clave and origen_clave is None:
        origen_clave = ORIGEN_MANUAL if contrasena_propia else ORIGEN_RECUPERADA
    fecha_exp = (
        str(fecha_expedicion_doc).strip() if fecha_expedicion_doc not in (None, "")
        else None
    )
    with _conectar() as conn:
        previo = conn.execute(
            "SELECT 1 FROM clientes_autorizados WHERE numero_documento = ?", (numero,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO clientes_autorizados
                (numero_documento, tipo_documento, contrasena_cifrada,
                 fecha_vencimiento, telefono, contrasena_propia, origen_clave,
                 fecha_expedicion_doc, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(numero_documento) DO UPDATE SET
                tipo_documento = excluded.tipo_documento,
                contrasena_cifrada = COALESCE(excluded.contrasena_cifrada,
                                              clientes_autorizados.contrasena_cifrada),
                fecha_vencimiento = excluded.fecha_vencimiento,
                telefono = COALESCE(excluded.telefono,
                                    clientes_autorizados.telefono),
                contrasena_propia = CASE
                    WHEN excluded.contrasena_cifrada IS NULL
                        THEN clientes_autorizados.contrasena_propia
                    ELSE excluded.contrasena_propia
                END,
                origen_clave = COALESCE(excluded.origen_clave,
                                        clientes_autorizados.origen_clave),
                fecha_expedicion_doc = COALESCE(
                    excluded.fecha_expedicion_doc,
                    clientes_autorizados.fecha_expedicion_doc)
            """,
            (numero, tipo_documento, clave, fecha_vencimiento, tel,
             1 if (clave and contrasena_propia) else 0, origen_clave, fecha_exp,
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {
        "numero_documento": numero,
        "nuevo": previo is None,
        "tiene_clave": clave is not None,
    }


def cliente_tiene_clave(numero_documento: str) -> bool:
    """True si el cliente está en el catálogo y ya tiene clave guardada."""
    numero = str(numero_documento).strip()
    with _conectar() as conn:
        row = conn.execute(
            "SELECT 1 FROM clientes_autorizados WHERE numero_documento = ? "
            "AND contrasena_cifrada IS NOT NULL "
            "AND TRIM(contrasena_cifrada) <> ''",
            (numero,),
        ).fetchone()
    return row is not None


def buscar_cliente(numero_documento: str) -> dict | None:
    """Devuelve el cliente (con credencial cifrada) o None si no existe."""
    numero = str(numero_documento).strip()
    with _conectar() as conn:
        row = conn.execute(
            "SELECT * FROM clientes_autorizados WHERE numero_documento = ?", (numero,)
        ).fetchone()
    return dict(row) if row else None


def listar_clientes(limite: int = 1000) -> list[dict]:
    """Lista clientes autorizados. NUNCA devuelve la credencial cifrada."""
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT numero_documento, tipo_documento, fecha_vencimiento, "
            "       telefono, contrasena_propia, origen_clave, "
            "       fecha_expedicion_doc, creado_en, "
            "       CASE WHEN contrasena_cifrada IS NOT NULL "
            "                 AND TRIM(contrasena_cifrada) <> '' "
            "            THEN 1 ELSE 0 END AS tiene_clave "
            "FROM clientes_autorizados ORDER BY creado_en DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(r) for r in rows]


def contar_clientes() -> int:
    with _conectar() as conn:
        row = conn.execute("SELECT COUNT(*) FROM clientes_autorizados").fetchone()
    return int(row[0]) if row else 0


def contar_clientes_sin_clave() -> int:
    """Clientes del catálogo que todavía no tienen clave DIAN guardada."""
    with _conectar() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM clientes_autorizados "
            "WHERE contrasena_cifrada IS NULL OR TRIM(contrasena_cifrada) = ''"
        ).fetchone()
    return int(row[0]) if row else 0


def eliminar_cliente(numero_documento: str, admin: str) -> dict:
    """Elimina un cliente autorizado. Solo admin."""
    numero = str(numero_documento).strip()
    with _conectar() as conn:
        cur = conn.execute(
            "DELETE FROM clientes_autorizados WHERE numero_documento = ?", (numero,)
        )
        if cur.rowcount == 0:
            raise AuthError(f"No se encontró el cliente '{numero}'.")
        conn.execute(
            "INSERT INTO registros (accion, quien, usuario, cuando) "
            "VALUES (?, ?, ?, ?)",
            ("eliminar_cliente", admin, numero,
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return {"numero_documento": numero, "eliminado": True}