"""
recuperacion — Flujo guiado para restablecer la contraseña DIAN desde la web.

El portal MUISCA entrega el código de verificación al contacto registrado del
usuario (correo/celular), así que el flujo no puede completarse de una sola
pasada automática: la sesión del navegador se mantiene viva entre llamadas y la
web pide el código y la nueva contraseña a la persona que opera (el contador o
el propio cliente).

Pasos (evidencia .inspeccion_flujo/20261017_login_recuperacion, 02-08):
  1. /recuperar-cuenta/autenticacion  -> tipo y número de documento
  2. /recuperar-cuenta/menu           -> "Enviar código"
  3. /recuperar-cuenta/cambiar-pass   -> código recibido
  4. /recuperar-cuenta/cambiar-pass   -> nueva contraseña + confirmación
  5. éxito -> aparece "Iniciar sesión" (la DIAN regenera la firma electrónica)

Requiere el navegador endurecido (app.navegador): sin él, el portal no habilita
ni el primer botón "Continuar" (señal anti-bot, ver navegador.py). El primer
paso exige además un captcha de Cloudflare (Turnstile) que solo una persona
puede superar: en local se usa ``asistido=True`` (ventana visible, la persona
resuelve el captcha y el resto sigue automático); en el servidor el flujo
automático queda deshabilitado hasta rediseñar.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from playwright.async_api import TimeoutError as PWTimeout
from playwright.async_api import async_playwright, expect

from .comun import TIMEOUT_LOGIN
from .navegador import (
    ARG_DNS_TURNSTILE,
    ARGS_CHROMIUM_EXTRA,
    ARGS_SERVIDOR,
    USER_AGENT_CHROME,
    lanzar_navegador,
    nuevo_contexto,
)

# Entrada del flujo: el SPA de identidad solo sirve /recuperar-cuenta al
# navegar dentro de la app (el deep-link directo responde 404 del servidor).
# Esta es la URL de acceso capturada en la evidencia (01_login.json); desde
# ahí el enlace "¿Olvidó su contraseña?" abre el formulario de recuperación.
URL_ENTRADA = (
    "https://muisca.dian.gov.co/WebIdentidadLogin/?ideRequest="
    "eyJjbGllbnRJZCI6IldvMGFLQWxCN3ZSUF8xNmZyUEkxeDlacGhCRWEiLCJyZWRpcmVjdF91cmki"
    "OiJodHRwOi8vbXVpc2NhLmRpYW4uZ292LmNvL0lkZW50aWRhZFJlc3RfTG9naW5GaWx0cm8vYXBp"
    "L3N0cy92MS9hdXRoL2NhbGxiYWNrP3JlZGlyZWN0X3VyaT1odHRwJTNBJTJGJTJGbXVpc2NhLmRp"
    "YW4uZ292LmNvJTJGV2ViQXJxdWl0ZWN0dXJhJTJGRGVMb2dpbi5mYWNlcyIsInJlc3BvbnNlVHlw"
    "ZSI6IiIsInNjb3BlIjoiIiwic3RhdGUiOiIiLCJub25jZSI6IiIsInBhcmFtcyI6eyJ0aXBvVXN1"
    "YXJpbyI6Im11aXNjYSJ9fQ%3D"
)
_ENLACE_RECUPERACION = "a:has-text('¿Olvidó su contraseña?')"

# Estados públicos del flujo (los consume la web)
PASO_MENU = "menu"      # en /menu: falta pulsar "Enviar código"
PASO_CODIGO = "codigo"  # en /cambiar-pass: falta el código recibido
PASO_CLAVE = "clave"    # en /cambiar-pass: falta la nueva contraseña
PASO_EXITO = "exito"
PASO_ERROR = "error"

# El botón que habilita la validación del formulario (anti-bot) y el submit real
_BTN_CONTINUAR = "button:has-text('Continuar')"
_BTN_ACTUALIZAR = "button:has-text('Actualizar contraseña y generar firma')"
_BTN_CLAVE_FINAL = "button:has-text('Continuar contraseña y generar firma')"
_BTN_EXITO = "button:has-text('Iniciar sesión')"


class ErrorRecuperacion(RuntimeError):
    """Error legible para mostrar al usuario (nunca incluye datos sensibles)."""


def _texto_tipo_documento(tipo: str) -> str:
    """Traduce el tipo de documento del sistema al texto de la lista DIAN."""
    t = (tipo or "").lower()
    if "extranjer" in t:
        return "Cédula de extranjería"
    if "nit" in t:
        return "NIT"
    if "pasaporte" in t:
        return "Pasaporte"
    if "cédula" in t or "cedula" in t or t.strip() == "cc":
        return "Cédula de ciudadanía"
    return tipo.strip() or "Cédula de ciudadanía"


class SesionRecuperacion:
    """Sesión Playwright viva del flujo DIAN de recuperación de contraseña.

    Uso: iniciar() -> enviar_codigo() -> aplicar_codigo(código) ->
    definir_clave(nueva, confirmación) -> cerrar(). Cualquier fallo lanza
    ErrorRecuperacion con mensaje apto para el usuario; el estado/URL queda a la
    vista en ``paso``/``mensaje`` para que la web lo muestre.

    Con ``asistido=True`` (uso local) la ventana es visible y el captcha del
    primer paso se espera a que la persona lo resuelva (ventana de
    ``espera_humano_seg`` segundos, 12 min por defecto).
    """

    def __init__(
        self,
        tipo_documento: str,
        numero_documento: str,
        dir_trabajo: Optional[Path] = None,
        asistido: bool = False,
        espera_humano_seg: int = 720,
    ):
        self.tipo_documento = tipo_documento
        self.numero_documento = str(numero_documento).strip()
        self.dir_trabajo = Path(dir_trabajo) if dir_trabajo else None
        self.asistido = asistido
        self.espera_humano_seg = int(espera_humano_seg)
        self.paso = ""
        self.mensaje = ""
        self._p = None
        self._browser = None
        self._context = None
        self.page = None

    # -- ciclo de vida -----------------------------------------------------
    async def iniciar(self) -> None:
        """Abre el navegador endurecido y ejecuta el paso de autenticación."""
        self._p = await async_playwright().start()
        try:
            self._browser = await lanzar_navegador(
                self._p,
                headless=not self.asistido,
                args_servidor=[*ARGS_SERVIDOR, ARG_DNS_TURNSTILE],
                args_extra=ARGS_CHROMIUM_EXTRA,
            )
            self._context = await nuevo_contexto(
                self._browser, user_agent=USER_AGENT_CHROME
            )
            self.page = await self._context.new_page()
            await self._paso_autenticacion()
        except ErrorRecuperacion:
            await self.cerrar()
            raise
        except Exception as exc:  # noqa: BLE001
            await self.captura("error_autenticacion")
            await self.cerrar()
            raise ErrorRecuperacion(
                f"No se pudo iniciar el flujo de recuperación: {type(exc).__name__}."
            ) from exc

    async def cerrar(self) -> None:
        """Cierra navegador y Playwright tolerando fallos parciales."""
        for cerrar in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._p, "stop", None),
        ):
            if cerrar:
                try:
                    await cerrar()
                except Exception:  # noqa: BLE001
                    pass
        self._context = self._browser = self._p = None

    # -- pasos -------------------------------------------------------------
    async def _paso_autenticacion(self) -> None:
        page = self.page
        try:
            await page.goto(URL_ENTRADA, wait_until="domcontentloaded", timeout=60_000)
            await page.click(_ENLACE_RECUPERACION, timeout=30_000)
            await page.wait_for_selector(
                "mat-select[name='tipoDocumento']", timeout=30_000
            )
        except PWTimeout as exc:
            raise ErrorRecuperacion(
                "No se pudo abrir la página de recuperación de la DIAN. "
                "Inténtalo de nuevo en unos minutos."
            ) from exc

        await page.click("mat-select[name='tipoDocumento']", timeout=TIMEOUT_LOGIN)
        await page.click(
            f"mat-option:has-text('{_texto_tipo_documento(self.tipo_documento)}')",
            timeout=TIMEOUT_LOGIN,
        )
        documento = page.locator("input[name='numDocumento']")
        try:
            await expect(documento).to_be_enabled(timeout=TIMEOUT_LOGIN)
        except (PWTimeout, AssertionError) as exc:
            raise ErrorRecuperacion(
                "El portal DIAN no habilitó el campo del documento. "
                "Inténtalo de nuevo en unos minutos."
            ) from exc
        # Tecleo real: la validación de Angular solo reacciona a pulsaciones
        # individuales (con fill() el formulario queda inválido y el botón
        # "Continuar" nunca se habilita).
        await documento.click()
        await documento.press_sequentially(self.numero_documento, delay=60)
        await page.keyboard.press("Tab")

        continuar = page.locator(_BTN_CONTINUAR).first
        actualizar = page.locator(_BTN_ACTUALIZAR)
        if self.asistido:
            # El captcha de Cloudflare (Turnstile) bloquea a los bots: con la
            # ventana a la vista la persona lo resuelve y aquí se espera.
            await self._esperar_avance_humano()
        else:
            if not await actualizar.count():
                # El primer "Continuar" valida el formulario; si el navegador
                # está bloqueado por la DIAN nunca se habilita (señal anti-bot).
                try:
                    await expect(continuar).to_be_enabled(timeout=30_000)
                except (PWTimeout, AssertionError) as exc:
                    raise ErrorRecuperacion(
                        "El portal DIAN no habilitó el formulario (posible bloqueo "
                        "del navegador). Intenta de nuevo en unos minutos."
                    ) from exc
                await continuar.click()
                try:
                    await page.wait_for_url(
                        "**/recuperar-cuenta/menu*", timeout=10_000
                    )
                except PWTimeout:
                    pass  # "Continuar" solo validó: el submit real es el otro botón

            if "/recuperar-cuenta/menu" not in page.url:
                try:
                    await expect(actualizar).to_be_enabled(timeout=30_000)
                except (PWTimeout, AssertionError) as exc:
                    raise ErrorRecuperacion(
                        "El portal DIAN no habilitó el formulario (posible bloqueo "
                        "del navegador). Intenta de nuevo en unos minutos."
                    ) from exc
                await actualizar.click()
                try:
                    await page.wait_for_url(
                        "**/recuperar-cuenta/menu*", timeout=60_000
                    )
                except PWTimeout as exc:
                    raise ErrorRecuperacion(
                        "La DIAN no avanzó a la pantalla de código; verifica el tipo "
                        "y número de documento e inténtalo de nuevo."
                    ) from exc

        self.paso = PASO_MENU
        self.mensaje = (
            "Documento validado. Pulsa «Enviar código»: la DIAN enviará el código "
            "al correo o celular registrado."
        )

    async def _esperar_avance_humano(self) -> None:
        """Modo asistido: espera a que la persona supere el captcha de la DIAN.

        Cloudflare Turnstile está diseñado para bloquear bots (en headless el
        reto nunca entrega token), así que con la ventana a la vista la persona
        lo resuelve en segundos. Aquí se detecta el avance por la URL y se pulsa
        «Continuar» o el botón del aviso de firma en cuanto se habiliten.
        """
        page = self.page
        self.mensaje = (
            "Resuelve el captcha en la ventana y el proceso continuará solo."
        )
        errores = (
            "No encontramos sus datos",
            "No hemos encontrado datos de contacto",
            "Su cuenta está deshabilitada",
        )
        limite = time.monotonic() + max(30, self.espera_humano_seg)
        while time.monotonic() < limite:
            if "/recuperar-cuenta/menu" in page.url:
                return
            for texto in errores:
                if await page.locator(f"text={texto}").count():
                    raise ErrorRecuperacion(
                        f"La DIAN respondió: «{texto}». Revisa el tipo y número de "
                        "documento."
                    )
            for selector in (_BTN_ACTUALIZAR, _BTN_CONTINUAR):
                boton = page.locator(selector).first
                try:
                    if await boton.count() and await boton.is_enabled():
                        await boton.click(timeout=2000)
                        break
                except Exception:  # noqa: BLE001
                    continue
            await page.wait_for_timeout(1000)
        alerta = await self._texto_alerta()
        raise ErrorRecuperacion(
            alerta or "No se detectó el avance tras el captcha; inténtalo de nuevo."
        )

    async def enviar_codigo(self) -> None:
        """Pulsa «Enviar código» en /recuperar-cuenta/menu."""
        if self.paso != PASO_MENU:
            raise ErrorRecuperacion("La sesión no está lista para enviar el código.")
        try:
            await self.page.click("button:has-text('Enviar código')", timeout=TIMEOUT_LOGIN)
            await self.page.wait_for_selector(
                "input[name='numDocumento'][placeholder*='recibido']", timeout=60_000
            )
        except Exception as exc:  # noqa: BLE001
            await self.captura("error_enviar_codigo")
            raise ErrorRecuperacion(
                "La DIAN no abrió el formulario del código. Intenta pulsar "
                "«Enviar código» de nuevo."
            ) from exc
        self.paso = PASO_CODIGO
        self.mensaje = "Código enviado. Escríbelo cuando llegue al correo o celular."

    async def reenviar_codigo(self) -> None:
        """Pulsa «Reenviar código» (cuando el anterior expiró o no llegó)."""
        if self.paso != PASO_CODIGO:
            raise ErrorRecuperacion("Aún no hay un código pendiente por verificar.")
        try:
            await self.page.click("button:has-text('Reenviar código')", timeout=TIMEOUT_LOGIN)
        except Exception as exc:  # noqa: BLE001
            raise ErrorRecuperacion(
                "No se pudo reenviar el código todavía; espera unos segundos e "
                "inténtalo de nuevo."
            ) from exc
        self.mensaje = "Código reenviado. Espera el nuevo mensaje del cliente."

    async def aplicar_codigo(self, codigo: str) -> None:
        """Escribe el código recibido y continúa hacia la definición de clave."""
        if self.paso != PASO_CODIGO:
            raise ErrorRecuperacion("La sesión no está esperando un código.")
        page = self.page
        campo = page.locator("input[name='numDocumento'][placeholder*='recibido']")
        # Tecleo real (ver _paso_autenticacion): fill() no dispara la validación.
        await campo.click()
        await campo.press_sequentially(codigo.strip(), delay=40)
        await page.keyboard.press("Tab")
        continuar = page.locator(_BTN_CONTINUAR).first
        try:
            await expect(continuar).to_be_enabled(timeout=TIMEOUT_LOGIN)
        except (PWTimeout, AssertionError) as exc:
            raise ErrorRecuperacion(
                "El portal DIAN no habilitó el botón para validar el código; "
                "espera unos segundos e inténtalo de nuevo."
            ) from exc
        await continuar.click()

        try:
            await page.locator("input[name='password']").wait_for(
                state="visible", timeout=30_000
            )
        except PWTimeout:
            alerta = await self._texto_alerta()
            self.mensaje = alerta or (
                "La DIAN no aceptó el código. Verifica que sea el último enviado "
                "o usa «Reenviar código»."
            )
            raise ErrorRecuperacion(self.mensaje)
        self.paso = PASO_CLAVE
        self.mensaje = "Código aceptado. Define la nueva contraseña."

    async def definir_clave(self, contrasena: str, confirmacion: str) -> None:
        """Escribe la nueva contraseña, la guarda en la DIAN y confirma el éxito."""
        if self.paso != PASO_CLAVE:
            raise ErrorRecuperacion("La sesión no está lista para definir la contraseña.")
        page = self.page
        campo1 = page.locator("input[name='password']")
        campo2 = page.locator("input[name='passwordConfirmacion']")
        # Tecleo real (ver _paso_autenticacion): fill() no dispara la validación.
        await campo1.click()
        await campo1.press_sequentially(contrasena, delay=30)
        try:
            await expect(campo2).to_be_enabled(timeout=TIMEOUT_LOGIN)
        except (PWTimeout, AssertionError) as exc:
            alerta = await self._texto_alerta()
            raise ErrorRecuperacion(
                alerta
                or "La DIAN no aceptó la nueva contraseña (revisa los requisitos "
                "que muestra la página)."
            ) from exc
        await campo2.click()
        await campo2.press_sequentially(confirmacion, delay=30)
        await page.keyboard.press("Tab")
        boton = page.locator(_BTN_CLAVE_FINAL)
        try:
            await expect(boton).to_be_enabled(timeout=TIMEOUT_LOGIN)
        except (PWTimeout, AssertionError) as exc:
            alerta = await self._texto_alerta()
            raise ErrorRecuperacion(
                alerta
                or "La DIAN no habilitó el botón de guardar; revisa la contraseña."
            ) from exc
        await boton.click()

        try:
            await page.locator(_BTN_EXITO).wait_for(state="visible", timeout=30_000)
        except PWTimeout:
            alerta = await self._texto_alerta()
            await self.captura("error_definir_clave")
            self.mensaje = alerta or (
                "La DIAN no confirmó el cambio de contraseña; inténtalo de nuevo."
            )
            raise ErrorRecuperacion(self.mensaje)
        self.paso = PASO_EXITO
        self.mensaje = "Contraseña actualizada correctamente en la DIAN."

    # -- utilidades ----------------------------------------------------------
    async def _texto_alerta(self) -> str:
        """Último mensaje visible de alerta/snackbar/validación del portal."""
        if not self.page:
            return ""
        for selector in (".mat-mdc-snack-bar-label", "[role='alert']", "mat-error"):
            try:
                local = self.page.locator(selector)
                if await local.count():
                    textos = [t.strip() for t in await local.all_inner_texts() if t.strip()]
                    if textos:
                        return textos[-1][:300]
            except Exception:  # noqa: BLE001
                continue
        return ""

    async def captura(self, prefijo: str) -> None:
        """Screenshot de diagnóstico (solo si hay directorio de trabajo)."""
        if not self.page or not self.dir_trabajo:
            return
        try:
            self.dir_trabajo.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=self.dir_trabajo / f"{prefijo}.png")
        except Exception:  # noqa: BLE001
            pass
