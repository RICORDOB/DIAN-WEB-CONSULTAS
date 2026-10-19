"""Utilidades compartidas por las herramientas locales de recuperación DIAN."""

from __future__ import annotations

import re
import secrets
import subprocess

_MAYUS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MINUS = "abcdefghijkmnopqrstuvwxyz"
_DIGITOS = "23456789"
_SIMBOLOS = "!@#$%*+-"
# Caracteres especiales aceptados por el portal de identidad de la DIAN.
_ESPECIALES_DIAN = r"#*()$%&/\\,¿?\-_@¡!+.="


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


def errores_politica_clave(clave: str) -> list[str]:
    """Lista los requisitos incumplidos por una clave según el portal DIAN.

    Réplica de las validaciones del módulo ``habilitar-cuenta``:
    6–15 caracteres, mínimo 2 letras, 1 número, 1 especial, mayúscula y
    minúscula, sin 6 caracteres iguales seguidos y sin acentos ni ñ.
    """
    clave = clave or ""
    errores: list[str] = []
    if not 6 <= len(clave) <= 15:
        errores.append("debe tener entre 6 y 15 caracteres")
    if len(re.findall(r"[A-Za-z]", clave)) < 2:
        errores.append("debe incluir al menos 2 letras")
    if not re.search(r"[0-9]", clave):
        errores.append("debe incluir al menos 1 número")
    if not re.search(rf"[{_ESPECIALES_DIAN}]", clave):
        errores.append(
            "debe incluir al menos 1 carácter especial (# * ( ) $ % & / \\ , ¿ ? - _ @ ¡ ! + . =)"
        )
    if not re.search(r"[A-Z]", clave) or not re.search(r"[a-z]", clave):
        errores.append("debe incluir mayúsculas y minúsculas")
    if re.search(r"(.)\1{5}", clave):
        errores.append("no puede repetir un carácter 6 veces seguidas")
    if re.search(r"[ñÑáéíóúÁÉÍÓÚ]", clave):
        errores.append("no puede incluir acentos ni la letra ñ")
    if re.search(r"\s", clave):
        errores.append("no puede incluir espacios")
    return errores


def generar_clave_dian(largo: int = 14) -> str:
    """Genera una clave que cumple la política del portal DIAN."""
    for _ in range(200):
        clave = generar_clave_segura(largo)
        if not errores_politica_clave(clave):
            return clave
    raise RuntimeError("No se pudo generar una clave que cumpla la política DIAN.")


def copiar_portapapeles(texto: str) -> bool:
    """Copia al portapapeles en macOS (pbcopy); tolera fallos."""
    try:
        subprocess.run(["pbcopy"], input=texto.encode(), check=True, timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False
