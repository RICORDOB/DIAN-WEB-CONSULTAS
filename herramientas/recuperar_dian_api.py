"""
recuperar_dian_api — Restablecimiento de la contraseña DIAN por API, sin navegador.

Llama directamente a los mismos servicios REST que consume el portal
muisca.dian.gov.co/WebIdentidadLogin. El captcha de Cloudflare (Turnstile) es un
control de la interfaz: no viaja en ninguna petición, por lo que este flujo no lo
necesita (verificado contra producción el 2026-10-18).

Pasos (idénticos a los del portal):
  1. Token de invitado:  POST /identidad/sts/v1/tokens/guest?auth=<base64(marca UTC)>
  2. Validar documento:  POST /identidadadministracion/auth/v1/usuarios
  3. Enviar código:      POST /identidadadministracion/auth/v1/usuarios/otp
  4. Cambiar contraseña: POST /identidadadministracion/auth/v1/usuarios/cambioClave
     La clave viaja cifrada AES-128/ECB/PKCS7 con llave = fecha local AAAAMMDD
     repetida dos veces, igual que la cifra el portal.

Uso:
    .venv/bin/python herramientas/recuperar_dian_api.py 1045498915
    .venv/bin/python herramientas/recuperar_dian_api.py 1045498915 --solo-consultar
    .venv/bin/python herramientas/recuperar_dian_api.py 1045498915 --tipo ce --medio email

Al terminar, guarda la nueva clave en el panel («Agregar / actualizar cliente»)
para dejarla cifrada en el sistema.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives import padding as relleno
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from herramientas.comun import copiar_portapapeles, generar_clave_segura  # noqa: E402

API_BASE = "https://api.dian.gov.co"
RUTA_TOKEN = "/identidad/sts/v1/tokens/guest"
RUTA_USUARIOS = "/identidadadministracion/auth/v1/usuarios"

TIPOS_DOCUMENTO = {
    "cc": "CC",
    "ce": "CE",
    "cd": "CD",
    "ti": "TI",
    "rc": "RC",
    "te": "TE",
    "ps": "PS",
    "pasaporte": "PS",
    "ppt": "PPT",
    "pep": "PEP",
    "defdian": "DEFDIAN",
    "ng": "REGNOID",
    "regnoid": "REGNOID",
}
_TIPOS_NUMERICOS = {"CC", "CE", "CD", "TI", "RC", "TE"}


class ErrorDIAN(Exception):
    """Error devuelto por los servicios de la DIAN o de red."""


def base64_utf8(texto: str) -> str:
    """Equivalente a btoa(unescape(encodeURIComponent(texto))) del portal."""
    return base64.b64encode(texto.encode("utf-8")).decode("ascii")


def clave_cifrado(fecha: date | None = None) -> str:
    """Llave AES del portal: AAAAMMDD local repetida dos veces (16 bytes)."""
    dia = (fecha or datetime.now()).strftime("%Y%m%d")
    return dia + dia


def cifrar_clave(clave: str, fecha: date | None = None) -> str:
    """Cifra la contraseña como CryptoJS en el portal (AES-128 ECB, PKCS7, base64)."""
    datos = clave.encode("utf-8")
    padder = relleno.PKCS7(128).padder()
    datos_rellenados = padder.update(datos) + padder.finalize()
    cifrador = Cipher(algorithms.AES(clave_cifrado(fecha).encode("ascii")), modes.ECB()).encryptor()
    return base64.b64encode(cifrador.update(datos_rellenados) + cifrador.finalize()).decode("ascii")


def enmascarar(valor: str) -> str:
    """Oculta datos personales en la salida de consola."""
    if "@" in valor:
        usuario, _, dominio = valor.partition("@")
        return f"{usuario[:3]}***@{dominio}"
    if len(valor) > 5:
        return f"{valor[:3]}***{valor[-2:]}"
    return "***"


class ClienteRecuperacionDIAN:
    """Cliente de los servicios oficiales de recuperación de contraseña DIAN."""

    def __init__(self, base: str = API_BASE, timeout: int = 30) -> None:
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._http = requests.Session()
        self._http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            }
        )
        self._id_token: str | None = None
        self._client_id: str | None = None
        self._expira_en: float = 0.0

    # --- infraestructura -------------------------------------------------

    def _renovar_token(self) -> None:
        """Pide un token de invitado (acceso anónimo, igual que el portal)."""
        marca = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        try:
            respuesta = self._http.post(
                f"{self._base}{RUTA_TOKEN}",
                params={"auth": base64_utf8(marca)},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ErrorDIAN(f"No se pudo conectar con la DIAN: {exc}") from exc
        datos = self._datos(respuesta)
        self._id_token = datos["idToken"]
        self._client_id = datos.get("clientId", "")
        self._expira_en = time.monotonic() + float(datos.get("expireIn", 900)) * 0.8

    def _asegurar_token(self) -> None:
        if not self._id_token or time.monotonic() >= self._expira_en:
            self._renovar_token()

    def _post(self, ruta: str, cuerpo: dict, reintentar: bool = True) -> dict:
        """POST autenticado; renueva el token una vez si la DIAN responde 401."""
        self._asegurar_token()
        try:
            respuesta = self._http.post(
                f"{self._base}{ruta}",
                json=cuerpo,
                headers={
                    "Authorization": f"Bearer {self._id_token}",
                    "ClientId": self._client_id or "",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ErrorDIAN(f"No se pudo conectar con la DIAN: {exc}") from exc
        if respuesta.status_code == 401 and reintentar:
            self._renovar_token()
            return self._post(ruta, cuerpo, reintentar=False)
        return self._datos(respuesta)

    @staticmethod
    def _datos(respuesta: requests.Response) -> dict:
        """Convierte la respuesta en dict y eleva ErrorDIAN si trae error."""
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ErrorDIAN(
                f"Respuesta no válida de la DIAN (HTTP {respuesta.status_code})."
            ) from exc
        codigo = datos.get("codigo")
        if respuesta.status_code >= 400 or codigo not in (None, 200, "200"):
            mensaje = datos.get("mensaje") or f"HTTP {respuesta.status_code}"
            raise ErrorDIAN(f"{mensaje} (código {codigo})")
        return datos

    # --- pasos del flujo ---------------------------------------------------

    def consultar_documento(self, tipo: str, numero: str) -> dict:
        """Valida el documento y devuelve los medios de recuperación registrados."""
        datos = self._post(RUTA_USUARIOS, {"documento": base64_utf8(f"{tipo}-{numero}")})
        return datos.get("datos", {})

    def solicitar_codigo(self, tipo: str, numero: str, medio: dict) -> None:
        """Pide a la DIAN enviar el código de seguridad al medio elegido."""
        self._post(
            f"{RUTA_USUARIOS}/otp",
            {
                "documento": base64_utf8(f"{tipo}-{numero}"),
                "medioRecuperacion": {"tipo": medio.get("tipo"), "valor": medio.get("valor")},
            },
        )

    def cambiar_clave(
        self,
        tipo: str,
        numero: str,
        clave: str,
        codigo: str,
        medio: dict,
        generar_firma: bool = False,
    ) -> dict:
        """Cambia la contraseña (el servidor valida el código OTP en esta llamada)."""
        return self._post(
            f"{RUTA_USUARIOS}/cambioClave",
            {
                "documento": base64_utf8(f"{tipo}-{numero}"),
                "password": cifrar_clave(clave),
                "otp": codigo,
                "medioRecuperacion": {"tipo": medio.get("tipo"), "valor": medio.get("valor")},
                "generarFirma": "true" if generar_firma else "false",
            },
        )


def elegir_medio(medios: list[dict], preferido: str | None) -> dict:
    """Elige el medio de recuperación (por preferencia o preguntando)."""
    if preferido:
        buscado = preferido.strip().lower()
        for medio in medios:
            if str(medio.get("tipo", "")).lower() == buscado:
                return medio
        raise ErrorDIAN(f"La DIAN no reporta un medio de tipo «{preferido}».")
    if len(medios) == 1:
        return medios[0]
    for indice, medio in enumerate(medios, 1):
        print(f"  {indice}. {medio.get('tipo')}: {enmascarar(str(medio.get('valor', '')))}")
    while True:
        eleccion = input("¿A cuál medio enviar el código? Número: ").strip()
        if eleccion.isdigit() and 1 <= int(eleccion) <= len(medios):
            return medios[int(eleccion) - 1]
        print("Opción no válida.")


def flujo(
    numero: str,
    tipo: str,
    medio_preferido: str | None,
    solo_consultar: bool,
    generar_firma: bool,
) -> int:
    cliente = ClienteRecuperacionDIAN()

    print("Consultando el documento en la DIAN (sin navegador, sin captcha)...")
    datos = cliente.consultar_documento(tipo, numero)
    medios = datos.get("mediosRecuperacion") or []
    if not medios:
        print("La DIAN no reporta medios de recuperación para este documento.")
        return 1

    print("\nMedios de recuperación registrados:")
    for indice, medio in enumerate(medios, 1):
        print(f"  {indice}. {medio.get('tipo')}: {enmascarar(str(medio.get('valor', '')))}")
    print(
        f"  Firma electrónica: {'sí' if datos.get('tieneFirmaElectronica') else 'no'}"
        f" · RUT: {'sí' if datos.get('tieneRut') else 'no'}"
    )
    if solo_consultar:
        return 0

    medio = elegir_medio(medios, medio_preferido)
    print(f"\nEnviando el código a {enmascarar(str(medio.get('valor', '')))}...")
    cliente.solicitar_codigo(tipo, numero, medio)
    print("Código enviado (vence en pocos minutos; revisa correo/celular del cliente).")

    codigo = input("Escribe el código recibido: ").strip()
    if not codigo:
        print("ERROR: sin código no se puede continuar.")
        return 1

    print("\nNueva contraseña (mismos requisitos que muestra la DIAN).")
    nueva = getpass.getpass("Escríbela o pulsa Enter para generar una segura: ").strip()
    if not nueva:
        nueva = generar_clave_segura()
        print(f"Clave generada: {nueva}")
        if copiar_portapapeles(nueva):
            print("(copiada al portapapeles)")

    confirmacion = input("\n¿Cambiar la contraseña en la DIAN ahora? (s/N): ").strip().lower()
    if confirmacion != "s":
        print("Cancelado: no se cambió nada.")
        return 0

    resultado = cliente.cambiar_clave(tipo, numero, nueva, codigo, medio, generar_firma)
    print("\nLISTO: " + (resultado.get("mensaje") or "contraseña actualizada en la DIAN."))
    print("Guárdala en el panel («Agregar / actualizar cliente») para dejarla cifrada.")
    return 0


def principal() -> int:
    parser = argparse.ArgumentParser(
        description="Recuperación de la contraseña DIAN por API oficial (sin navegador)."
    )
    parser.add_argument("numero", help="Número de documento del cliente")
    parser.add_argument(
        "--tipo",
        default="cc",
        help="Tipo de documento: cc, ce, cd, ti, rc, te, ps, ppt, pep, ext, defdian, ng",
    )
    parser.add_argument(
        "--medio",
        default=None,
        help="Medio de recuperación preferido (p. ej. email); si no, se pregunta",
    )
    parser.add_argument(
        "--solo-consultar",
        action="store_true",
        help="Solo valida el documento y muestra los medios registrados",
    )
    parser.add_argument(
        "--generar-firma",
        action="store_true",
        help="Generar también la firma electrónica con la nueva clave",
    )
    args = parser.parse_args()

    tipo = TIPOS_DOCUMENTO.get(args.tipo.strip().lower())
    if not tipo:
        print(f"ERROR: tipo de documento no reconocido: {args.tipo}")
        print("Válidos: " + ", ".join(sorted(TIPOS_DOCUMENTO)))
        return 2

    numero = args.numero.strip()
    if tipo in _TIPOS_NUMERICOS and not numero.isdigit():
        print("ERROR: para ese tipo de documento el número debe ser solo dígitos.")
        return 2

    try:
        return flujo(numero, tipo, args.medio, args.solo_consultar, args.generar_firma)
    except ErrorDIAN as exc:
        print(f"\nERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(principal())
