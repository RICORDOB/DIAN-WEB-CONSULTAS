"""
Fase 3: consulta real de recibos. Busca el recibo 490 de la declaración de
renta de 1045498915. Vuelca la tabla de resultados (Recibo No | Concepto |
Fecha de Pago | ValorTotal | operaciones) para identificar el recibo del
30-09-2026 y confirmar el selector de descarga.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runner import DianRunner  # noqa: E402


def _dir_trabajo() -> Path:
    base = Path(".inspeccion_declaraciones")
    base.mkdir(exist_ok=True)
    return base / datetime.now().strftime("%Y%m%d_%H%M%S")


async def _dump(page, dir_out: Path, nombre: str):
    html = await page.content()
    (dir_out / f"{nombre}.html").write_text(html, encoding="utf-8")
    await page.screenshot(path=str(dir_out / f"{nombre}.png"), full_page=True)
    print(f"[volcado] {nombre}.html | {nombre}.png")


async def _tabla_recibos(page) -> list[dict]:
    """Extrae filas de la tabla con datos de recibos (datatable)."""
    filas = []
    try:
        tabla = page.locator("table[class*='dataTable'], table[id*='listaRecibos'], table[class*='ric-datatable']")
        # buscar por encabezado "Recibo No"
        for tb in page.locator("table"):
            try:
                txt = await tb.inner_text()
                if "Recibo No" in txt and "Fecha de Pago" in txt:
                    trs = tb.locator("tbody tr, tr:has(td)")
                    n = await trs.count()
                    for i in range(n):
                        celdas = (await trs.nth(i).locator("td").all_inner_texts()) if n else []
                        filas.append([c.strip() for c in celdas])
            except Exception:
                pass
    except Exception:
        pass
    return filas


async def _generar(page, dir_out: Path, concepto_val: str) -> None:
    await page.wait_for_timeout(2500)
    await _dump(page, dir_out, "01_recibos_form")

    # La "Consulta Avanzada" (donde viven los selects) es un panel colapsable.
    try:
        await page.evaluate(
            "SimpleTogglePanelManager.toggleOnClient("
            "'vistaConsultaYPagoRecibos:frmConsultaYPagoRecibos:_id44')"
        )
        print("[ok] Panel 'Consulta Avanzada' abierto vía toggleOnClient")
        await page.wait_for_timeout(800)
    except Exception as exc:
        print(f"[aviso] toggle JS falló: {type(exc).__name__}: {exc}")

    # Asegurar que el cuerpo del panel quede visible (a veces display:none persiste).
    await page.evaluate(
        "document.getElementById("
        "'vistaConsultaYPagoRecibos:frmConsultaYPagoRecibos:_id44_body'"
        ").style.display='block'"
    )

    fmt = page.locator("select[id$=':listaOpciones']")
    await fmt.select_option("490")
    print("[ok] Formato -> 490 (Recibo oficial de pago Impuestos Nacionales)")
    await page.wait_for_timeout(800)

    # Si seleccionamos 490, el combinado de conceptos debería recargar
    await _dump(page, dir_out, "02_recibos_formato490")

    conc = page.locator("select[id$=':listaConceptos']")
    await conc.select_option(concepto_val)
    print(f"[ok] Concepto -> {concepto_val}")
    await page.wait_for_timeout(800)

    # Botón buscar (submit AJAX _id55, único del form)
    btn = page.locator("input[id$=':_id55']")
    await btn.click(force=True)
    print("[ok] Clic en buscar")
    await page.wait_for_timeout(4000)
    await page.wait_for_load_state("networkidle")

    await _dump(page, dir_out, "03_resultados")
    filas = await _tabla_recibos(page)
    print(f"\n[foreach] {len(filas)} fila(s) en la tabla de recibos:")
    for f in filas:
        print("  -", " | ".join(f[:6]))


async def main(cedula: str, clave: str, concepto: str) -> None:
    dir_out = _dir_trabajo()
    print(f"[trabajo] {dir_out}")
    creds = {
        "tipo_documento": "Cédula de Ciudadanía",
        "numero_documento": cedula,
        "contrasena": clave,
    }
    runner = DianRunner(job_dir=dir_out)
    try:
        p, browser, context, page = await runner._abrir_sesion(creds)
    except Exception as exc:
        print(f"[fatal] Login falló: {type(exc).__name__}: {exc}")
        return
    try:
        await page.wait_for_timeout(2000)
        btn = page.locator("input[id='vistaDashboard:frmDashboard:btnPagoRecibos']")
        await btn.first.click(timeout=10000, force=True)
        print("[ok] Clic en btnPagoRecibos")
        await page.wait_for_timeout(3500)
        await _generar(page, dir_out, concepto)
        print(f"\n[fin] Volcados en {dir_out}")
    finally:
        await context.close()
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consulta real de recibos 490 en MUISCA")
    parser.add_argument("--cedula", required=True)
    parser.add_argument("--clave", required=True)
    parser.add_argument("--concepto", default="4", help="Valor de listaConceptos (4=RENTA, 6=RETENCION)")
    args = parser.parse_args()
    asyncio.run(main(args.cedula, args.clave, args.concepto))