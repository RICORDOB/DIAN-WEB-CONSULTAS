"""
recuperacion_api — Flujo guiado de recuperación de contraseña DIAN por API.

Mismo contrato que ``app.recuperacion.SesionRecuperacion`` (por eso la web y la
 cola de recuperaciones de ``app.main`` no cambian), pero sin navegador: llama a
los servicios REST oficiales de la DIAN (herramientas/recuperar_dian_api.py).
El captcha del portal es un control de la interfaz que no viaja en ninguna
petición (verificado contra producción el 2026-10-18), así que este flujo
funciona también en el servidor.

Igual que en el portal real, el código OTP no se valida al escribirlo: la DIAN
lo comprueba en la llamada de cambio de clave. Si el código es incorrecto o
vencido, la sesión vuelve al paso «codigo» con mensaje explícito.

Uso: iniciar() -> enviar_codigo(medio) -> aplicar_codigo(código) ->
definir_clave(nueva, confirmación) -> cerrar().
"""

from __future__ import annotations

import asyncio
import unicodedata
from pathlib import Path
from typing import Optional

from herramientas.recuperar_dian_api import (
    TIPOS_DOCUMENTO,
    ClienteRecuperacionDIAN,
    ErrorDIAN,
    enmascarar,
)

from .recuperacion import (
    PASO_CLAVE,
    PASO_CODIGO,
    PASO_EXITO,
    PASO_MENU,
    ErrorRecuperacion,
)


def _sin_acentos(texto: str) -> str:
    """«Cédula» -> «Cedula»: facilita comparar textos que llegan con tildes."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(c)
    )


def _codigo_tipo(texto: str) -> str:
    """Traduce el tipo de documento del panel («Cédula de Ciudadanía») al código DIAN (CC)."""
    t = _sin_acentos((texto or "").strip().lower())
    if not t:
        return "CC"
    if t in TIPOS_DOCUMENTO:
        return TIPOS_DOCUMENTO[t]
    if "extranjer" in t:
        return "TE" if "tarjeta" in t else "CE"
    if "ciudadania" in t or t == "cedula":
        return "CC"
    if "identidad" in t:
        return "TI"
    if "registro civil" in t:
        return "RC"
    if "registraduria" in t or "sin identificacion" in t:
        return "REGNOID"
    if "pasaporte" in t:
        return "PS"
    if "permanencia" in t or t == "pep":
        return "PEP"
    if "proteccion temporal" in t or t == "ppt":
        return "PPT"
    if "nit" in t:
        return "NIT"
    return t.upper()


def _etiqueta_medio(medio: dict) -> str:
    """Etiqueta legible y enmascarada del medio (nunca expone el dato completo)."""
    valor_crudo = str(medio.get("valor", ""))
    tipo = str(medio.get("tipo", "")).lower()
    valor = enmascarar(valor_crudo)
    if tipo == "email" or "@" in valor_crudo:
        return f"Correo: {valor}"
    if tipo.startswith("tel") or "celular" in tipo:
        return f"Celular: {valor}"
    return f"{medio.get('tipo', '')}: {valor}"


def _es_codigo_invalido(mensaje: str) -> bool:
    """Distingue el rechazo del OTP (para regresar al paso del código)."""
    m = _sin_acentos(mensaje).lower()
    return "ingreso no valido" in m or "codigo no" in m or "codigo invalido" in m


class SesionRecuperacionAPI:
    """Sesión del flujo DIAN de recuperación de contraseña vía API oficial."""

    def __init__(
        self,
        tipo_documento: str,
        numero_documento: str,
        dir_trabajo: Optional[Path] = None,
    ):
        self.tipo_documento = tipo_documento
        self.numero_documento = str(numero_documento).strip()
        # Se acepta por compatibilidad con la firma de SesionRecuperacion; la
        # API no genera capturas ni archivos.
        self.dir_trabajo = Path(dir_trabajo) if dir_trabajo else None
        self.tipo = _codigo_tipo(tipo_documento)
        self.paso = ""
        self.mensaje = ""
        # Medios enmascarados para el selector del panel: [{tipo, etiqueta}].
        self.medios_ui: list[dict] = []
        self._cliente: ClienteRecuperacionDIAN | None = None
        self._medios: list[dict] = []
        self._medio: dict | None = None
        self._codigo: str | None = None
        self._generar_firma = False

    # -- ciclo de vida -----------------------------------------------------
    async def iniciar(self) -> None:
        """Valida el documento y lista los medios de recuperación registrados."""
        self._cliente = ClienteRecuperacionDIAN()
        datos = await self._llamar(
            self._cliente.consultar_documento, self.tipo, self.numero_documento
        )
        self._medios = list(datos.get("mediosRecuperacion") or [])
        self._generar_firma = bool(datos.get("tieneFirmaElectronica"))
        if not self._medios:
            raise ErrorRecuperacion(
                "La DIAN no reporta correo ni celular registrados para este "
                "documento. Pídele al cliente actualizarlos en el portal DIAN."
            )
        # El correo primero (el mensaje suele llegar más rápido), luego el resto.
        self._medios.sort(key=lambda m: str(m.get("tipo", "")).lower() != "email")
        self.medios_ui = [
            {"tipo": str(m.get("tipo", "")), "etiqueta": _etiqueta_medio(m)}
            for m in self._medios
        ]
        self.paso = PASO_MENU
        self.mensaje = (
            "Documento validado. Elige a dónde enviar el código y pulsa "
            "«Enviar código»: llegará al contacto registrado del cliente."
        )

    async def cerrar(self) -> None:
        """Olvida token, código y contraseña (no hay navegador que cerrar)."""
        self._cliente = None
        self._medio = None
        self._codigo = None
        self._generar_firma = False

    # -- pasos -------------------------------------------------------------
    async def enviar_codigo(self, medio: str | None = None) -> None:
        """Pide el código al medio elegido en el panel (o al primero si no se indicó)."""
        if self.paso != PASO_MENU:
            raise ErrorRecuperacion("La sesión no está lista para enviar el código.")
        elegido: dict | None = None
        if medio:
            buscado = medio.strip().lower()
            elegido = next(
                (
                    m
                    for m in self._medios
                    if str(m.get("tipo", "")).lower() == buscado
                ),
                None,
            )
            if elegido is None:
                raise ErrorRecuperacion(
                    f"La DIAN no reporta un medio de recuperación «{medio}»."
                )
        elif self._medios:
            elegido = self._medios[0]
        if elegido is None:
            raise ErrorRecuperacion("No hay medios de recuperación disponibles.")
        await self._llamar(
            self._cliente.solicitar_codigo, self.tipo, self.numero_documento, elegido
        )
        self._medio = elegido
        self.paso = PASO_CODIGO
        self.mensaje = (
            f"Código enviado a {_etiqueta_medio(elegido)}. "
            "Pídeselo al cliente cuando llegue."
        )

    async def reenviar_codigo(self) -> None:
        """Vuelve a pedir el código al mismo medio (para códigos vencidos)."""
        if self.paso != PASO_CODIGO or not self._medio:
            raise ErrorRecuperacion("Aún no hay un código pendiente por reenviar.")
        await self._llamar(
            self._cliente.solicitar_codigo, self.tipo, self.numero_documento, self._medio
        )
        self.mensaje = "Código reenviado. Espera el nuevo mensaje del cliente."

    async def aplicar_codigo(self, codigo: str) -> None:
        """Guarda el código: la DIAN lo valida al cambiar la clave (como el portal)."""
        if self.paso != PASO_CODIGO:
            raise ErrorRecuperacion("La sesión no está esperando un código.")
        self._codigo = codigo.strip()
        self.paso = PASO_CLAVE
        self.mensaje = "Código recibido. Define la nueva contraseña y guárdala."

    async def definir_clave(self, contrasena: str, confirmacion: str) -> None:
        """Cambia la contraseña en la DIAN (aquí se valida el código OTP)."""
        if self.paso != PASO_CLAVE:
            raise ErrorRecuperacion(
                "La sesión no está lista para definir la contraseña."
            )
        if not self._codigo or not self._medio:
            raise ErrorRecuperacion("Faltan el código o el medio de recuperación.")
        try:
            await asyncio.to_thread(
                self._cliente.cambiar_clave,
                self.tipo,
                self.numero_documento,
                contrasena,
                self._codigo,
                self._medio,
                self._generar_firma,
            )
        except ErrorDIAN as exc:
            texto = str(exc)
            if _es_codigo_invalido(texto):
                self._codigo = None
                self.paso = PASO_CODIGO
                self.mensaje = (
                    "La DIAN no aceptó el código. Verifica el último recibido o "
                    "usa «Reenviar código»."
                )
                raise ErrorRecuperacion(self.mensaje) from exc
            raise ErrorRecuperacion(texto) from exc
        self.paso = PASO_EXITO
        self.mensaje = "Contraseña actualizada correctamente en la DIAN."

    # -- utilidades ----------------------------------------------------------
    async def _llamar(self, funcion, *args):
        """Ejecuta la llamada de red (``requests``) fuera del event loop."""
        try:
            return await asyncio.to_thread(funcion, *args)
        except ErrorDIAN as exc:
            raise ErrorRecuperacion(str(exc)) from exc
