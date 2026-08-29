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
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

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
def _conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            "CREATE INDEX IF NOT EXISTS idx_consultas_estado ON consultas(estado)"
        )
        admin_user = os.environ.get("APP_ADMIN_USER")
        admin_pass = os.environ.get("APP_ADMIN_PASS")
        if admin_user and admin_pass:
            existe = conn.execute(
                "SELECT 1 FROM usuarios WHERE usuario = ? AND rol = 'admin'",
                (admin_user,),
            ).fetchone()
            if not existe:
                h, s = _hash_password(admin_pass)
                conn.execute(
                    "INSERT INTO usuarios (usuario, hash, salt, rol, estado, creado_en) "
                    "VALUES (?, ?, ?, 'admin', 'aprobado', ?)",
                    (admin_user, h, s, time.strftime("%Y-%m-%d %H:%M:%S")),
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
            "SELECT id, usuario, rol, estado, creado_en FROM usuarios "
            "ORDER BY creado_en DESC"
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


def es_admin(usuario: str) -> bool:
    with _conectar() as conn:
        row = conn.execute(
            "SELECT rol FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
    return bool(row and row["rol"] == "admin")


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