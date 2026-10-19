"""Avisos push de vencimientos de claves DIAN.

Cada vencimiento genera hasta tres avisos, todos a las 8:00 AM hora Bogotá:

1. Cinco días antes del vencimiento.
2. El lunes de la semana del vencimiento.
3. El día del vencimiento.

Reglas de coincidencia (máximo un aviso por cliente por día):

* ``hoy``    → ``venc == hoy``.
* ``semana`` → ``hoy`` es el lunes de la semana del vencimiento.
* ``cinco``  → ``hoy == venc - 5 días`` y el lunes de esa semana cae
  después de ese día. Si el lunes avisa antes (venc en domingo) o cae el
  mismo día (venc en sábado), el aviso de cinco días se omite o fusiona.

El marcador ``vencimientos_ultima_ejecucion`` (tabla ``config``) evita repetir
el envío en cada chequeo del vigilante.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import auth, push

TZ_BOGOTA = timezone(timedelta(hours=-5))  # Colombia no usa horario de verano
HORA_AVISO = 8
CLAVE_ULTIMA_EJECUCION = "vencimientos_ultima_ejecucion"

_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MESES = ("ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic")

TIPOS = ("hoy", "semana", "cinco")


def _parsear_fecha(valor) -> Optional[date]:
    """Convierte 'YYYY-MM-DD' (o date) en date; None si falta o es inválida."""
    if isinstance(valor, date):
        return valor
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError:
        return None


def _lunes_de(d: date) -> date:
    """Lunes de la semana ISO a la que pertenece d."""
    return d - timedelta(days=d.weekday())


def avisos_del_dia(clientes: list[dict], hoy: date) -> dict[str, list[str]]:
    """Clasifica los clientes que deben recibir aviso hoy.

    Devuelve ``{"hoy": [...], "semana": [...], "cinco": [...]}`` con los
    números de documento. Máximo un aviso por cliente, con prioridad
    hoy > semana > cinco. Fechas inválidas o vacías se ignoran.
    """
    resultado: dict[str, list[str]] = {tipo: [] for tipo in TIPOS}
    for c in clientes:
        venc = _parsear_fecha(c.get("fecha_vencimiento"))
        if venc is None:
            continue
        numero = str(c.get("numero_documento", "")).strip()
        if not numero:
            continue
        lunes = _lunes_de(venc)
        cinco = venc - timedelta(days=5)
        if venc == hoy:
            resultado["hoy"].append(numero)
        elif lunes == hoy:
            resultado["semana"].append(numero)
        elif cinco == hoy and lunes > cinco:
            resultado["cinco"].append(numero)
    return resultado


def _fecha_txt(d: date) -> str:
    return f"{_DIAS[d.weekday()]} {d.day} {_MESES[d.month - 1]}"


def textos_aviso(tipo: str, numeros: list[str], hoy: date) -> tuple[str, str]:
    """Título y cuerpo de la notificación para un grupo de cédulas."""
    n = len(numeros)
    lista = ", ".join(numeros[:4]) + (f" y {n - 4} más" if n > 4 else "")
    plural = "cédula" if n == 1 else "cédulas"
    if tipo == "hoy":
        titulo = "Vencen HOY claves DIAN"
        cuerpo = f"{n} {plural}: {lista} — {_fecha_txt(hoy)}"
    elif tipo == "semana":
        titulo = "Vencen esta semana claves DIAN"
        cuerpo = f"{n} {plural}: {lista} — {_fecha_txt(hoy)}"
    else:
        titulo = "Claves DIAN que vencen en 5 días"
        cuerpo = f"{n} {plural}: {lista} — {_fecha_txt(hoy + timedelta(days=5))}"
    return titulo, cuerpo


def revisar_y_notificar(ahora: Optional[datetime] = None,
                        forzar: bool = False) -> dict:
    """Revisa el catálogo y envía los avisos push del día.

    Fuera de las 8:00 AM Bogotá no hace nada (salvo ``forzar=True``). Deja el
    día marcado en ``config`` para no repetir en cada chequeo; si no hay
    suscriptores no marca, para que el aviso alcance a salir si alguien
    activa las notificaciones más tarde ese mismo día.
    """
    if ahora is None:
        ahora = datetime.now(TZ_BOGOTA)
    elif ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=TZ_BOGOTA)
    else:
        ahora = ahora.astimezone(TZ_BOGOTA)
    hoy = ahora.date()
    if not forzar and ahora.hour < HORA_AVISO:
        return {"omitido": "antes de las 8:00 AM (Bogotá)"}
    hoy_txt = hoy.isoformat()
    if not forzar and auth.config_get(CLAVE_ULTIMA_EJECUCION) == hoy_txt:
        return {"omitido": "ya ejecutado hoy", "fecha": hoy_txt}

    clientes = auth.listar_clientes(limite=5000)
    avisos = avisos_del_dia(clientes, hoy)
    usuarios = auth.listar_usuarios_suscritos()

    enviados = 0
    for tipo in TIPOS:
        numeros = avisos[tipo]
        if not numeros:
            continue
        titulo, cuerpo = textos_aviso(tipo, numeros, hoy)
        for usuario in usuarios:
            enviados += push.notificar(
                usuario, titulo, cuerpo, url="/dev", tag="vencimientos"
            )

    if usuarios or forzar:
        auth.config_set(CLAVE_ULTIMA_EJECUCION, hoy_txt)

    return {
        "fecha": hoy_txt,
        "avisos": {tipo: len(avisos[tipo]) for tipo in TIPOS},
        "suscritos": len(usuarios),
        "enviados": enviados,
    }


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Avisos de vencimiento DIAN: simulación y envío manual.")
    parser.add_argument("--hoy", metavar="YYYY-MM-DD",
                        help="Simula los avisos de esa fecha (no envía nada).")
    parser.add_argument("--enviar", action="store_true",
                        help="Ejecuta la revisión real ahora (ignora hora y marcador).")
    args = parser.parse_args()
    auth.iniciar_db()  # aplica migraciones pendientes antes de leer el catálogo

    if args.enviar:
        print(json.dumps(revisar_y_notificar(forzar=True),
                         ensure_ascii=False, indent=2))
        return

    hoy = date.fromisoformat(args.hoy) if args.hoy else datetime.now(TZ_BOGOTA).date()
    clientes = auth.listar_clientes(limite=5000)
    avisos = avisos_del_dia(clientes, hoy)
    print(f"Avisos simulados para {hoy.isoformat()} ({_fecha_txt(hoy)}):")
    hubo = False
    for tipo in TIPOS:
        numeros = avisos[tipo]
        if not numeros:
            continue
        hubo = True
        titulo, cuerpo = textos_aviso(tipo, numeros, hoy)
        print(f"  [{tipo}] {titulo}")
        print(f"         {cuerpo}")
    if not hubo:
        print("  (sin avisos para esta fecha)")


if __name__ == "__main__":
    _cli()
