"""cifrado — Cifrado simétrico para credenciales de clientes at-rest.

Deriva una clave Fernet (32 bytes) de APP_SECRET_KEY usando HMAC-SHA256 con un
contexto fijo, de modo que no hace falta añadir dependencias ni guardar claves
adicionales en el entorno: la misma APP_SECRET_KEY del servicio protege los
datos. Cambiar APP_SECRET_KEY invalida los datos cifrados (por diseño).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from binascii import Error as BinasciiError

# Contexto de derivación: distinto del que firma sesiones para evitar reutilizar
# accidentalmente la misma clave para propósitos diferentes.
_CONTEXTO = b"exorenta-clientes"

# Longitud mínima de APP_SECRET_KEY recomendada. En desarrollo se permite una
# clave de desarrollo si APP_ENV=dev (misma regla que auth.py).
_MIN_SECRET = 16


def _clave_fernet() -> bytes:
    secreto = os.environ.get("APP_SECRET_KEY", "")
    if not secreto:
        if os.environ.get("APP_ENV") == "dev":
            secreto = "clave-dev-cifrado-no-usar"
        else:
            raise RuntimeError(
                "APP_SECRET_KEY no está definida; no se pueden cifrar datos de clientes."
            )
    if len(secreto) < _MIN_SECRET:
        raise RuntimeError("APP_SECRET_KEY es demasiado corta para cifrar clientes.")

    info = hmac.new(secreto.encode("utf-8"), _CONTEXTO, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(bytes.fromhex(info))


def cifrar(texto: str) -> str:
    """Devuelve el texto cifrado en base64 (URL-safe)."""
    import cryptography.fernet as _fernet

    f = _fernet.Fernet(_clave_fernet())
    return f.encrypt(texto.encode("utf-8")).decode("ascii")


def descifrar(cifrado: str) -> str:
    """Devuelve el texto original. Lanza ValueError si la clave es inválida o
    cambió la APP_SECRET_KEY (los datos cifrados quedan ilegibles)."""
    import cryptography.fernet as _fernet
    from cryptography.fernet import InvalidToken

    f = _fernet.Fernet(_clave_fernet())
    try:
        return f.decrypt(cifrado.encode("ascii")).decode("utf-8")
    except (BinasciiError, InvalidToken, ValueError) as exc:
        raise ValueError("No se pudo descifrar la credencial del cliente.") from exc