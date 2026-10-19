"""
habilitacion_api — Primera contraseña DIAN por API (flujo «Usuario Nuevo»).

Réplica del módulo ``habilitar-cuenta`` del portal de identidad de la DIAN
(muisca.dian.gov.co/WebIdentidadLogin), pero sin navegador ni captcha:

  1. Validar documento + fecha de expedición:
     POST /identidadadministracion/v1/cuentausuarios  {"operacion": {"codigo": "1"}}
  2. Pedir el OTP al correo/celular registrados:
     POST /identidadadministracion/auth/v1/usuarios/otp
  3. Validar el OTP (control de interfaz del portal):
     GET  https://apiazure.dian.gov.co/identidad/2fa/v1/otps/{otp}?userId=...
  4. Crear la clave (el servidor valida el OTP de nuevo):
     POST /identidadadministracion/v1/cuentausuarios  {"operacion": {"codigo": "2"}}

La contraseña viaja en Base64 (no con el AES de la recuperación). El OTP lo
recibe el cliente: es el único paso que no se puede automatizar.

Uso: iniciar(fecha_expedicion) -> enviar_codigo(medio) -> validar_codigo(otp)
     -> definir_clave(nueva, confirmación) -> cerrar().
"""

from __future__ import annotations

import asyncio

from herramientas.comun import errores_politica_clave
from herramientas.recuperar_dian_api import (
    ClienteRecuperacionDIAN,
    CodigoOTPInvalido,
    ErrorDIAN,
    normalizar_fecha_expedicion,
)

from .recuperacion import (
    PASO_CLAVE,
    PASO_CODIGO,
    PASO_EXITO,
    PASO_MENU,
    ErrorRecuperacion,
)
from .recuperacion_api import _codigo_tipo, _es_codigo_invalido, _etiqueta_medio


def _es_verdadero(valor) -> bool:
    """La DIAN devuelve ``"true"``/``"false"`` como texto en estos campos."""
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() == "true"


def _tipo_rta(respuesta: dict) -> str:
    """Normaliza ``codigoRta.tipoRta`` (puede venir como texto o número)."""
    return str((respuesta or {}).get("tipoRta", "")).strip()


class SesionHabilitacionAPI:
    """Sesión guiada para habilitar la cuenta DIAN de un cliente sin clave."""

    def __init__(
        self,
        tipo_documento: str,
        numero_documento: str,
        dir_trabajo=None,
    ):
        self.tipo_documento = tipo_documento
        self.numero_documento = str(numero_documento).strip()
        # Se acepta por compatibilidad con la firma de SesionRecuperacion; la
        # API no genera capturas ni archivos.
        self.dir_trabajo = dir_trabajo
        self.tipo = _codigo_tipo(tipo_documento)
        self.paso = ""
        self.mensaje = ""
        self.fecha_expedicion = ""
        # Medios enmascarados para el selector del panel: [{tipo, etiqueta}].
        self.medios_ui: list[dict] = []
        self.tiene_rut = False
        self.tiene_firma = False
        self._cliente: ClienteRecuperacionDIAN | None = None
        self._medios: list[dict] = []
        self._medio: dict | None = None
        self._codigo: str | None = None

    # -- ciclo de vida -----------------------------------------------------
    async def iniciar(self, fecha_expedicion: str) -> None:
        """Valida el documento y la fecha; lista los medios registrados."""
        try:
            self.fecha_expedicion = normalizar_fecha_expedicion(fecha_expedicion)
        except ErrorDIAN as exc:
            raise ErrorRecuperacion(str(exc)) from exc

        self._cliente = ClienteRecuperacionDIAN()
        respuesta = await self._llamar(
            self._cliente.consultar_habilitacion,
            self.tipo,
            self.numero_documento,
            self.fecha_expedicion,
        )
        tipo_rta = _tipo_rta(respuesta)
        if tipo_rta == "2":
            raise ErrorRecuperacion(
                "La DIAN no encontró una cuenta con ese documento y fecha de "
                "expedición. Verifica que la fecha coincida con el RUT."
            )
        if tipo_rta == "3":
            raise ErrorRecuperacion(
                "Ese documento ya tiene una cuenta DIAN habilitada. Usa "
                "«Restablecer contraseña» si el cliente no recuerda la clave."
            )
        if tipo_rta != "1":
            detalle = (respuesta or {}).get("mensaje") or "La DIAN no confirmó los datos."
            raise ErrorRecuperacion(str(detalle))

        datos = (respuesta or {}).get("datos") or {}
        self._medios = list(datos.get("mediosRecuperacion") or [])
        self.tiene_rut = _es_verdadero(datos.get("tieneRut"))
        self.tiene_firma = _es_verdadero(datos.get("tieneFirmaElectronica"))
        if not self._medios:
            if not self.tiene_rut:
                raise ErrorRecuperacion(
                    "La DIAN no reporta correo, celular ni RUT para este "
                    "documento. El cliente debe actualizar sus datos en la DIAN "
                    "o el trámite debe hacerse de forma asistida."
                )
            raise ErrorRecuperacion(
                "La DIAN no reporta correo ni celular registrados para este "
                "documento. El cliente debe actualizarlos en el RUT/portal DIAN "
                "antes de habilitar la cuenta."
            )

        # El correo primero: el mensaje suele llegar más rápido.
        self._medios.sort(key=lambda m: str(m.get("tipo", "")).lower() != "email")
        self.medios_ui = [
            {"tipo": str(m.get("tipo", "")), "etiqueta": _etiqueta_medio(m)}
            for m in self._medios
        ]
        self.paso = PASO_MENU
        self.mensaje = (
            "Documento y fecha validados. Elige a dónde enviar el código y pulsa "
            "«Enviar código»: llegará al contacto registrado del cliente."
        )

    async def cerrar(self) -> None:
        """Olvida cliente, medio y código (no hay navegador que cerrar)."""
        self._cliente = None
        self._medio = None
        self._codigo = None
        self._medios = []
        self.medios_ui = []

    # -- pasos -------------------------------------------------------------
    async def enviar_codigo(self, medio: str | None = None) -> None:
        """Pide a la DIAN enviar el OTP al medio elegido."""
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
                    f"La DIAN no reporta un medio de contacto «{medio}»."
                )
        elif self._medios:
            elegido = self._medios[0]
        if elegido is None:
            raise ErrorRecuperacion("No hay medios de contacto disponibles.")

        await self._llamar(
            self._cliente.solicitar_codigo,
            self.tipo,
            self.numero_documento,
            elegido,
        )
        self._medio = elegido
        self.paso = PASO_CODIGO
        self.mensaje = (
            f"Código enviado a {_etiqueta_medio(elegido)}. "
            "Pídeselo al cliente cuando llegue."
        )

    async def reenviar_codigo(self) -> None:
        """Vuelve a pedir el código al mismo medio."""
        if self.paso != PASO_CODIGO or not self._medio:
            raise ErrorRecuperacion("Aún no hay un código pendiente por reenviar.")
        await self._llamar(
            self._cliente.solicitar_codigo,
            self.tipo,
            self.numero_documento,
            self._medio,
        )
        self.mensaje = "Código reenviado. Espera el nuevo mensaje del cliente."

    async def validar_codigo(self, codigo: str) -> None:
        """Valida el OTP contra el servicio 2FA antes de pedir la clave."""
        if self.paso != PASO_CODIGO:
            raise ErrorRecuperacion("La sesión no está esperando un código.")
        codigo = (codigo or "").strip()
        if not codigo:
            raise ErrorRecuperacion("Escribe el código que recibió el cliente.")
        try:
            await self._llamar(
                self._cliente.validar_codigo_otp,
                self.tipo,
                self.numero_documento,
                codigo,
            )
        except CodigoOTPInvalido as exc:
            self.paso = PASO_CODIGO
            self.mensaje = (
                "La DIAN no aceptó el código. Verifica el último recibido o "
                "usa «Reenviar código»."
            )
            raise ErrorRecuperacion(self.mensaje) from exc
        self._codigo = codigo
        self.paso = PASO_CLAVE
        self.mensaje = "Código validado. Define la nueva contraseña de la cuenta."

    async def definir_clave(self, contrasena: str, confirmacion: str) -> None:
        """Crea la contraseña y habilita la cuenta en la DIAN."""
        if self.paso != PASO_CLAVE:
            raise ErrorRecuperacion(
                "La sesión no está lista para definir la contraseña."
            )
        if not self._codigo:
            raise ErrorRecuperacion("Falta el código de seguridad.")
        if contrasena != confirmacion:
            raise ErrorRecuperacion("Las contraseñas no coinciden.")
        errores = errores_politica_clave(contrasena)
        if errores:
            raise ErrorRecuperacion(
                "La contraseña no cumple los requisitos de la DIAN: "
                + "; ".join(errores)
                + "."
            )

        try:
            respuesta = await self._llamar(
                self._cliente.crear_cuenta_habilitada,
                self.tipo,
                self.numero_documento,
                self.fecha_expedicion,
                contrasena,
                self._codigo,
            )
        except ErrorRecuperacion as exc:
            if _es_codigo_invalido(str(exc)):
                self._codigo = None
                self.paso = PASO_CODIGO
                self.mensaje = (
                    "La DIAN no aceptó el código. Verifica el último recibido o "
                    "usa «Reenviar código»."
                )
                raise ErrorRecuperacion(self.mensaje) from exc
            raise

        codigo_rta = (respuesta or {}).get("codigoRta") or {}
        tipo_rta = _tipo_rta(codigo_rta)
        if tipo_rta == "3":
            raise ErrorRecuperacion(
                "La cuenta ya estaba habilitada. Usa «Restablecer contraseña» "
                "si el cliente no recuerda la clave."
            )
        if tipo_rta == "2":
            raise ErrorRecuperacion(
                "La DIAN rechazó los datos del documento. Verifica la fecha de "
                "expedición e inténtalo de nuevo."
            )
        if tipo_rta != "1":
            detalle = codigo_rta.get("mensaje") or "La DIAN no confirmó la habilitación."
            raise ErrorRecuperacion(str(detalle))

        self.paso = PASO_EXITO
        self._codigo = None
        self.mensaje = (
            "Cuenta habilitada correctamente en la DIAN. Guarda la contraseña "
            "en el catálogo para poder consultar."
        )

    # -- utilidades ----------------------------------------------------------
    async def _llamar(self, funcion, *args):
        """Ejecuta la llamada de red (``requests``) fuera del event loop."""
        try:
            return await asyncio.to_thread(funcion, *args)
        except CodigoOTPInvalido:
            raise
        except ErrorDIAN as exc:
            raise ErrorRecuperacion(str(exc)) from exc
