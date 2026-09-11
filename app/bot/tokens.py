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