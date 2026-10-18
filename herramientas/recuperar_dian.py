"""
recuperar_dian — Restablecimiento asistido de la contraseña DIAN (uso local).

Abre una ventana real de Chrome y automatiza todo el flujo de recuperación; la
persona solo resuelve el captcha de Cloudflare cuando la DIAN lo pida
(Turnstile está hecho para bloquear bots: en headless nunca entrega token).
El código de verificación llega al correo/celular registrado por el cliente y
se escribe en este terminal; la nueva clave puede generarse segura.

Uso:
    .venv/bin/python herramientas/recuperar_dian.py 1045498915
    .venv/bin/python herramientas/recuperar_dian.py 1045498915 --tipo ce
    .venv/bin/python herramientas/recuperar_dian.py 1045498915 --espera-captcha 300

Al terminar, guarda la nueva clave en el panel («Agregar / actualizar
cliente») para dejarla cifrada en el sistema.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.recuperacion import ErrorRecuperacion, SesionRecuperacion  # noqa: E402
from herramientas.comun import copiar_portapapeles, generar_clave_segura  # noqa: E402


async def flujo(numero: str, tipo: str, espera_captcha: int) -> int:
    sesion = SesionRecuperacion(
        tipo, numero, asistido=True, espera_humano_seg=espera_captcha
    )
    try:
        print("Se abrirá una ventana de Chrome con el portal de la DIAN.")
        print("Cuando aparezca el captcha, resuélvelo ahí: el resto es automático.")
        await sesion.iniciar()
        print("\nDocumento validado por la DIAN.")

        print("Pulsando «Enviar código»...")
        await sesion.enviar_codigo()
        print("Código enviado al correo o celular registrado por el cliente.")
        codigo = input("Escribe el código recibido: ").strip()
        if not codigo:
            print("ERROR: sin código no se puede continuar.")
            return 1
        await sesion.aplicar_codigo(codigo)
        print("Código aceptado.")

        print("Nueva contraseña (debe cumplir los requisitos que muestra la DIAN).")
        nueva = getpass.getpass(
            "Escríbela o pulsa Enter para generar una segura: "
        ).strip()
        if not nueva:
            nueva = generar_clave_segura()
            print(f"Clave generada: {nueva}")
            if copiar_portapapeles(nueva):
                print("(copiada al portapapeles)")
        await sesion.definir_clave(nueva, nueva)
        print("\nLISTO: contraseña actualizada en la DIAN (firma regenerada).")
        print("Guárdala en el panel («Agregar / actualizar cliente») para dejarla")
        print("cifrada en el sistema.")
        return 0
    except ErrorRecuperacion as exc:
        print(f"\nERROR: {exc}")
        return 1
    finally:
        await sesion.cerrar()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recuperación asistida de la contraseña DIAN (uso local)."
    )
    parser.add_argument("numero", help="Número de documento del cliente")
    parser.add_argument(
        "--tipo",
        default="cc",
        help="Tipo de documento: cc, ce, nit, ti, pasaporte, nuip (por defecto cc)",
    )
    parser.add_argument(
        "--espera-captcha",
        type=int,
        default=720,
        help="Segundos máximos de espera del captcha humano (por defecto 720)",
    )
    args = parser.parse_args()

    numero = args.numero.strip()
    if args.tipo.lower() in ("cc", "ce", "ti", "nuip") and not numero.isdigit():
        print("ERROR: para ese tipo de documento el número debe ser solo dígitos.")
        return 2
    try:
        return asyncio.run(flujo(numero, args.tipo, args.espera_captcha))
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
