"""tokens — Firmado de cookies de chat y tokens de descarga de 1-uso.

Reutiliza URLSafeTimedSerializer con la misma APP_SECRET_KEY del servicio. Las
conversaciones del bot son anónimas: la cookie `bot` solo identifica el chat,
no a un usuario de la app. Los tokens de descarga vencen a los 15 minutos y
solo pueden consumirse una vez (se registran en memoria al emitirlos).
"""

from __future__ import annotations

import os
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_serializer = URLSafeTimedSerializer(
    os.environ.get("APP_SECRET_KEY", "clave-dev-chat-no-usar")
)

# Tokens de descarga emitidos y aún no consumidos: token -> {"usado": bool}
DURACION_DESCARGA_SEG = 15 * 60
_tokens_descarga: dict[str, bool] = {}

# Enlaces de activación de la cuenta DIAN. No son de un solo uso: el token
# deja de servir cuando el cliente ya tiene clave en el catálogo. La fecha de
# expedición viaja firmada para que el enlace no dependa de la BD al abrirlo.
SAL_ACTIVACION = "activacion-cuenta-dian"
DURACION_ACTIVACION_SEG = int(os.environ.get("APP_ACTIVACION_DIAS", "7")) * 86400


def crear_cookie_chat(chat_id: str) -> str:
    """Firma el chat_id en una cookie anónima de 12 h (renovable al usarse)."""
    return _serializer.dumps({"chat": chat_id})


def leer_cookie_chat(cookie: str | None) -> str | None:
    """Devuelve el chat_id firmado en la cookie, o None si es inválida/vencida."""
    try:
        data = _serializer.loads(cookie or "", max_age=12 * 3600)
        return data.get("chat")
    except (BadSignature, SignatureExpired, TypeError):
        return None


def emitir_token_descarga(chat_id: str, job_id: str) -> str:
    """Emite un token de descarga de un solo uso de 15 minutos."""
    token = _serializer.dumps({"chat": chat_id, "job_id": job_id})
    _tokens_descarga[token] = False
    return token


def consumir_token_descarga(token: str) -> tuple[str, str] | None:
    """Consume un token y devuelve (chat_id, job_id). None si es inválido,
    vencido o ya usado. El uso queda marcado aunque el job ya no exista."""
    try:
        data = _serializer.loads(token, max_age=DURACION_DESCARGA_SEG)
    except (BadSignature, SignatureExpired, TypeError):
        return None
    # El token solo es válido mientras exista sin marcar como usado; la
    # primera descarga lo elimina y anula cualquiera posterior.
    if token in _tokens_descarga and not _tokens_descarga[token]:
        _tokens_descarga[token] = True
        return (data["chat"], data["job_id"])
    return None


# ---------------------------------------------------------------------------
# Activación de la cuenta DIAN (primera clave)
# ---------------------------------------------------------------------------
def emitir_token_activacion(
    numero_documento: str, tipo_documento: str, fecha_expedicion: str
) -> str:
    """Firma un enlace de activación para un cliente sin clave DIAN."""
    return _serializer.dumps(
        {
            "proposito": "activacion",
            "doc": str(numero_documento).strip(),
            "tipo": str(tipo_documento).strip(),
            "fecha": str(fecha_expedicion).strip(),
        },
        salt=SAL_ACTIVACION,
    )


def leer_token_activacion(token: str) -> dict | None:
    """Devuelve los datos firmados del enlace, o None si es inválido/venció.

    El token no autoriza nada por sí solo: la DIAN exige el OTP que recibe el
    cliente. Solo identifica al cliente y su fecha de expedición.
    """
    try:
        data = _serializer.loads(
            token or "", salt=SAL_ACTIVACION, max_age=DURACION_ACTIVACION_SEG
        )
    except (BadSignature, SignatureExpired, TypeError):
        return None
    if data.get("proposito") != "activacion" or not data.get("doc"):
        return None
    return data