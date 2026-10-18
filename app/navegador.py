"""
navegador — Lanzamiento endurecido del navegador para la automatización DIAN.

Causa raíz (evidencia en .inspeccion_flujo/20261017_login_recuperacion): el
portal MUISCA no habilita la validación de sus formularios cuando el navegador
expone banderas de automatización (--enable-automation / AutomationControlled);
en un Google Chrome sin dichas banderas el formulario se comporta igual que con
un usuario real.

Estrategia:
  - Preferir Google Chrome real (channel="chrome", instalado en el Dockerfile);
    el Chromium de Playwright queda como respaldo automático.
  - Retirar --enable-automation y desactivar AutomationControlled.
  - Contexto con huella de usuario real: locale es-CO, zona horaria de Bogotá
    y UA de Chrome estable (los contenedores reportan "HeadlessChrome").
  - add_init_script neutraliza navigator.webdriver como red de seguridad.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

_log = logging.getLogger(__name__)

# Flags anti-detección comunes a Chrome y Chromium (no retirar).
ARGS_ANTIBOT = ["--disable-blink-features=AutomationControlled"]

# Flags operativas de servidor/contenedor (Render): aplican tanto a Chrome
# como a Chromium (Chrome en Docker también requiere /dev/shm y sandbox
# desactivados).
ARGS_SERVIDOR = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

# Flags exclusivas del Chromium empaquetado: Chrome estable no soporta
# --single-process.
ARGS_CHROMIUM_EXTRA = ["--single-process"]

# Cloudflare Turnstile (el captcha de la DIAN) entrega el reto desde hosts
# *.challenges.cloudflare.com; `brunhild` solo publica registros AAAA (IPv6)
# y en redes sin IPv6 el reto no carga (ERR_NAME_NOT_RESOLVED). Esta regla
# fija la IPv4 anycast equivalente; se usa solo en los flujos con captcha.
ARG_DNS_TURNSTILE = (
    "--host-resolver-rules=MAP brunhild.challenges.cloudflare.com 104.18.17.146"
)

# UA de Chrome estable: evita que los servidores reporten "HeadlessChrome".
USER_AGENT_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Red de seguridad: el portal consulta navigator.webdriver para bloquear la
# validación de formularios en navegadores automatizados.
_SCRIPT_ANTIBOT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => false});"
    "Object.defineProperty(navigator, 'languages', {get: () => ['es-CO', 'es']});"
)


async def lanzar_navegador(
    p,
    *,
    headless: bool,
    slow_mo: int = 0,
    args_servidor: Optional[Sequence[str]] = None,
    args_extra: Optional[Sequence[str]] = None,
):
    """Lanza Chrome real si está instalado; si no, el Chromium de Playwright.

    ``args_servidor`` aplica a ambos navegadores; ``args_extra`` solo al
    respaldo Chromium (p. ej. --single-process).
    """
    comun = {
        "headless": headless,
        "slow_mo": slow_mo,
        "ignore_default_args": ["--enable-automation"],
    }
    comunes = [*ARGS_ANTIBOT, *(args_servidor or [])]
    try:
        return await p.chromium.launch(channel="chrome", args=comunes, **comun)
    except Exception as exc:  # noqa: BLE001 - Chrome no disponible -> respaldo
        _log.warning(
            "Chrome real no disponible (%s: %s); usando Chromium de Playwright.",
            type(exc).__name__,
            exc,
        )
        return await p.chromium.launch(
            args=[*comunes, *(args_extra or [])], **comun
        )


async def nuevo_contexto(
    browser,
    *,
    accept_downloads: bool = True,
    user_agent: Optional[str] = None,
):
    """Contexto con huella de usuario real (locale, zona horaria y webdriver).

    El UA por defecto es el natural del navegador lanzado; los contenedores
    (Render) deben pasar ``USER_AGENT_CHROME`` para no delatar el modo headless.
    """
    context = await browser.new_context(
        accept_downloads=accept_downloads,
        locale="es-CO",
        timezone_id="America/Bogota",
        viewport={"width": 1366, "height": 768},
        **({"user_agent": user_agent} if user_agent else {}),
    )
    await context.add_init_script(_SCRIPT_ANTIBOT)
    return context
