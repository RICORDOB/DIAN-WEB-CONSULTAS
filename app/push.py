"""Notificaciones push (Web Push / VAPID) para la PWA instalable."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from . import auth

_data_dir = Path(os.environ.get("APP_DATA_DIR", "data"))
_VAPID_FILE = _data_dir / "vapid.json"
_VAPID_SUBJECT = "mailto:consultas@dian-web.local"
_CLAVE_CONFIG_VAPID = "vapid_private_pem"


def _vapid_private_pem() -> str:
    """Llave privada VAPID (PEM PKCS8). Se persiste en la tabla `config` la
    primera vez para que las suscripciones sobrevivan a reinicios y redespliegues.

    En el despliegue sobre Turso la clave queda en la nube y es estable entre
    deploys; en local sin Turso persiste en la BD sqlite local.
    """
    persistida = auth.config_get(_CLAVE_CONFIG_VAPID)
    if persistida:
        return persistida
    # Compatibilidad con instalaciones antiguas que usaban el archivo vapid.json
    try:
        if _VAPID_FILE.exists():
            datos = json.loads(_VAPID_FILE.read_text(encoding="utf-8"))
            if datos.get("private_pem"):
                auth.config_set(_CLAVE_CONFIG_VAPID, datos["private_pem"])
                return datos["private_pem"]
    except Exception:
        pass

    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    priv_pem = v.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    auth.config_set(_CLAVE_CONFIG_VAPID, priv_pem)
    return priv_pem


def clave_publica() -> str:
    """Llave pública VAPID (P-256, punto sin comprimir, urlsafe b64), que el
    navegador usa como applicationServerKey al suscribirse."""
    priv = load_pem_private_key(_vapid_private_pem().encode(), password=None)
    return base64.urlsafe_b64encode(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    ).rstrip(b"=").decode()


def notificar(usuario: str, titulo: str, cuerpo: str,
              url: str = "/panel", tag: str = "consulta") -> int:
    """Envía a todas las suscripciones activas del usuario. Las que la DIAN dio
    de baja (404/410) se eliminan. Devuelve el número de notificaciones enviadas."""
    from pywebpush import WebPushException, webpush

    priv_pem = _vapid_private_pem()
    enviadas = 0
    for sub in auth.listar_suscripciones(usuario):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps({
                    "titulo": titulo, "cuerpo": cuerpo, "url": url, "tag": tag,
                }),
                vapid_private_key=priv_pem,
                vapid_claims={"sub": _VAPID_SUBJECT},
                ttl=3600,
            )
            enviadas += 1
        except WebPushException as exc:
            # La suscripción ya no es válida: se quita de la tabla.
            if getattr(exc, "response", None) is not None and \
                    exc.response.status_code in (404, 410):
                auth.eliminar_suscripcion(sub["endpoint"])
        except Exception:
            pass
    return enviadas