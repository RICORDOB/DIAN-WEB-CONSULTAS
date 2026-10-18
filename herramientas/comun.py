"""Utilidades compartidas por las herramientas locales de recuperación DIAN."""

from __future__ import annotations

import secrets
import subprocess

_MAYUS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MINUS = "abcdefghijkmnopqrstuvwxyz"
_DIGITOS = "23456789"
_SIMBOLOS = "!@#$%*+-"


def generar_clave_segura(largo: int = 14) -> str:
    """Clave segura sin caracteres ambiguos (réplica de la del panel)."""
    todos = _MAYUS + _MINUS + _DIGITOS + _SIMBOLOS
    clave = [
        secrets.choice(_MAYUS),
        secrets.choice(_MINUS),
        secrets.choice(_DIGITOS),
        secrets.choice(_SIMBOLOS),
    ]
    clave += [secrets.choice(todos) for _ in range(max(0, largo - 4))]
    secrets.SystemRandom().shuffle(clave)
    return "".join(clave)


def copiar_portapapeles(texto: str) -> bool:
    """Copia al portapapeles en macOS (pbcopy); tolera fallos."""
    try:
        subprocess.run(["pbcopy"], input=texto.encode(), check=True, timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False
