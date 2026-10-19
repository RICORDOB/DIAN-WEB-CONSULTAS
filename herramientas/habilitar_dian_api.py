"""
habilitar_dian_api — Primera contraseña DIAN por API (flujo «Usuario Nuevo»).

Hace lo mismo que el portal cuando un cliente nunca ha tenido clave, pero sin
navegador ni captcha:

  1. Valida documento + fecha de expedición (dato del RUT).
  2. Envía un OTP al correo/celular registrado en la DIAN.
  3. Valida el OTP.
  4. Crea la contraseña y habilita la cuenta.

El OTP lo recibe el cliente: pídeselo y escríbelo en la terminal (nunca por el
chat). La clave nueva se muestra una sola vez; guárdala en el panel para que el
sistema pueda consultar.

Uso:
    .venv/bin/python herramientas/habilitar_dian_api.py 1045498915 \
        --fecha-expedicion 2010-05-20
    .venv/bin/python herramientas/habilitar_dian_api.py 1045498915 \
        --fecha-expedicion 2010-05-20 --solo-consultar
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from herramientas.comun import (  # noqa: E402
    copiar_portapapeles,
    generar_clave_dian,
)
from herramientas.recuperar_dian_api import (  # noqa: E402
    TIPOS_DOCUMENTO,
    ClienteRecuperacionDIAN,
    CodigoOTPInvalido,
    ErrorDIAN,
    _TIPOS_NUMERICOS,
    elegir_medio,
    enmascarar,
    normalizar_fecha_expedicion,
)


def flujo(
    numero: str,
    tipo: str,
    fecha_expedicion: str,
    medio_preferido: str | None,
    solo_consultar: bool,
) -> int:
    cliente = ClienteRecuperacionDIAN()

    print("Consultando el documento en la DIAN (sin navegador, sin captcha)...")
    respuesta = cliente.consultar_habilitacion(tipo, numero, fecha_expedicion)
    tipo_rta = str(respuesta.get("tipoRta", "")).strip()

    if tipo_rta == "2":
        print(
            "La DIAN no encontró una cuenta con ese documento y fecha de "
            "expedición. Verifica que la fecha coincida con el RUT."
        )
        return 1
    if tipo_rta == "3":
        print(
            "Ese documento ya tiene una cuenta DIAN habilitada. Usa "
            "herramientas/recuperar_dian_api.py si no recuerda la clave."
        )
        return 1
    if tipo_rta != "1":
        print("La DIAN no confirmó los datos:", respuesta.get("mensaje") or tipo_rta)
        return 1

    datos = respuesta.get("datos") or {}
    medios = list(datos.get("mediosRecuperacion") or [])
    tiene_rut = str(datos.get("tieneRut", "")).lower() == "true"
    tiene_firma = str(datos.get("tieneFirmaElectronica", "")).lower() == "true"

    print("\nDatos registrados en la DIAN:")
    print(f"  RUT: {'sí' if tiene_rut else 'no'}")
    print(f"  Firma electrónica: {'sí' if tiene_firma else 'no'}")
    if not medios:
        if not tiene_rut:
            print(
                "  Sin correo/celular ni RUT: el cliente debe actualizar sus "
                "datos en la DIAN o hacer el trámite asistido."
            )
        else:
            print(
                "  Sin correo ni celular registrados: el cliente debe "
                "actualizarlos en el RUT/portal DIAN antes de habilitar."
            )
        return 1

    print("  Medios de contacto:")
    for indice, medio in enumerate(medios, 1):
        print(f"    {indice}. {medio.get('tipo')}: {enmascarar(str(medio.get('valor', '')))}")

    if solo_consultar:
        return 0

    medio = elegir_medio(medios, medio_preferido)
    confirmacion = input(
        f"\n¿Enviar el código a {enmascarar(str(medio.get('valor', '')))}? (s/N): "
    ).strip().lower()
    if confirmacion != "s":
        print("Cancelado: no se envió ningún código.")
        return 0

    cliente.solicitar_codigo(tipo, numero, medio)
    print("Código enviado. Pídeselo al cliente (vence en pocos minutos).")
    codigo = input("Escribe el código recibido: ").strip()
    if not codigo:
        print("ERROR: sin código no se puede continuar.")
        return 1

    try:
        cliente.validar_codigo_otp(tipo, numero, codigo)
    except CodigoOTPInvalido:
        print("ERROR: la DIAN no aceptó el código (inválido, usado o vencido).")
        return 1
    print("Código válido.")

    print("\nNueva contraseña (mismos requisitos que muestra la DIAN).")
    nueva = getpass.getpass("Escríbela o pulsa Enter para generar una segura: ").strip()
    if not nueva:
        nueva = generar_clave_dian()
        print(f"Clave generada: {nueva}")
        if copiar_portapapeles(nueva):
            print("(copiada al portapapeles)")

    confirmar_clave = input(
        "\n¿Habilitar la cuenta en la DIAN con esta contraseña? (s/N): "
    ).strip().lower()
    if confirmar_clave != "s":
        print("Cancelado: no se habilitó la cuenta.")
        return 0

    resultado = cliente.crear_cuenta_habilitada(
        tipo, numero, fecha_expedicion, nueva, codigo
    )
    codigo_rta = resultado.get("codigoRta") or {}
    tipo_final = str(codigo_rta.get("tipoRta", "")).strip()
    if tipo_final == "3":
        print("La cuenta ya estaba habilitada. No se cambió la contraseña.")
        return 1
    if tipo_final != "1":
        print(
            "ERROR: la DIAN no habilitó la cuenta:",
            codigo_rta.get("mensaje") or tipo_final,
        )
        return 1

    print("\nLISTO: cuenta habilitada en la DIAN.")
    print("Guarda la contraseña en el panel («Agregar / actualizar cliente»).")
    return 0


def principal() -> int:
    parser = argparse.ArgumentParser(
        description="Habilitación de la cuenta DIAN por API oficial (primera clave)."
    )
    parser.add_argument("numero", help="Número de documento del cliente")
    parser.add_argument(
        "--tipo",
        default="cc",
        help="Tipo de documento: cc, ce, cd, ti, rc, te, ps, ppt, pep, defdian, ng",
    )
    parser.add_argument(
        "--fecha-expedicion",
        required=True,
        help="Fecha de expedición del documento tal como figura en el RUT (AAAA-MM-DD)",
    )
    parser.add_argument(
        "--medio",
        default=None,
        help="Medio de contacto preferido (p. ej. email); si no, se pregunta",
    )
    parser.add_argument(
        "--solo-consultar",
        action="store_true",
        help="Solo valida documento/fecha y muestra los medios registrados",
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
        fecha = normalizar_fecha_expedicion(args.fecha_expedicion)
        return flujo(numero, tipo, fecha, args.medio, args.solo_consultar)
    except ErrorDIAN as exc:
        print(f"\nERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(principal())
