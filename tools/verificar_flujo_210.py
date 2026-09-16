"""
Verificación del flujo real: Diligenciar y presentar → Formulario 210 →
Declaraciones de renta presentadas → pagar → SI → calendario → generar recibo.

Vuelca HTML + capturas en cada paso para derivar selectores exactos.
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
    base = Path(".inspeccion_flujo")
    base.mkdir(exist_ok=True)
    return base / datetime.now().strftime("%Y%m%d_%H%M%S")


async def _dump(page, dir_out: Path, nombre: str):
    html = await page.content()
    (dir_out / f"{nombre}.html").write_text(html, encoding="utf-8")
    await page.screenshot(path=str(dir_out / f"{nombre}.png"), full_page=True)
    print(f"[volcado] {nombre}.html | {nombre}.png")


async def _prologo_ids(page, *patrones: str) -> None:
    """Busca ids que contengan los patrones y su texto/src."""
    html = await page.content()
    vistos = set()
    print(f"\n[foreach] ids con {patrones}:")
    for m in re.finditer(r'id="([^"]+)"', html):
        iid = m.group(1)
        if not any(p.lower() in iid.lower() for p in patrones):
            continue
        if iid in vistos:
            continue
        vistos.add(iid)
        n = m.start()
        seg = html[max(0, n - 120):n + 300]
        src = re.search(r'src="([^"]*)"', seg)
        val = re.search(r'value="([^"]*)"', seg)
        onclick = re.search(r"onclick=\"([^\"]{0,80})", seg)
        print(f"  {iid}")
        if src:
            print(f"      src={src.group(1).split('/')[-1]}")
        if val:
            print(f"      value={val.group(1)}")
        if onclick:
            print(f"      onclick={onclick.group(1)}")
    print()


# Alias compat
async def _ids(page, *patrones: str) -> None:
    await _prologo_ids(page, *patrones)


async def _tablas(page) -> None:
    """Detecta tablas y sus primeras celdas con textos relevantes."""
    html = await page.content()
    print("\n[foreach] Tablas con 'Declaraciones'/'Renta'/'Acciones':")
    for m in re.finditer(r'<table[^>]*id="([^"]+)"[^>]*>(.*?)</table>', html, flags=re.S):
        tid, body = m.group(1), m.group(2)
        texto = re.sub(r"<[^>]+>", " ", body)
        texto = re.sub(r"\s+", " ", texto).strip()
        if any(k in texto.lower() for k in ("declarac", "renta", "acciones", "año", "a\u00f1o")) and texto:
            print(f"  [{tid}] :: {texto[:120]}")
    print()


async def main(cedula: str, clave: str) -> None:
    dir_out = _dir_trabajo()
    print(f"[trabajo] {dir_out}")
    creds = {"tipo_documento": "Cédula de Ciudadanía",
             "numero_documento": cedula, "contrasena": clave}
    runner = DianRunner(job_dir=dir_out)
    try:
        p, browser, context, page = await runner._abrir_sesion(creds)
    except Exception as exc:
        print(f"[fatal] Login falló: {type(exc).__name__}: {exc}")
        return
    try:
        await page.wait_for_timeout(2500)
        await _dump(page, dir_out, "01_dashboard")
        await _ids(page, "btnDiligenciar", "btnformulario", "btn210", "Diligenciar")

        # Paso 1: clic en "Diligenciar y presentar"
        candidatos = [
            "input[id='vistaDashboard:frmDashboard:btnDiligenciarYpresentar']",
            "input[id*='btnDiligenciarYpresentar']",
            "input[id*='btnDiligenciarPresentar']",
            "input[id$=':btnDiligenciarPresentar']",
            "input[src*='diligenciarPresentar']",
            "a:has-text('Diligenciar y presentar')",
        ]
        clicado = False
        for sel in candidatos:
            try:
                if await page.locator(sel).count():
                    await page.locator(sel).first.click(force=True, timeout=8000)
                    print(f"[ok] Clic 1 en {sel}")
                    clicado = True
                    break
            except Exception as exc:
                print(f"[aviso] {sel}: {type(exc).__name__}")
        if not clicado:
            try:
                await page.get_by_role("button", name="Diligenciar y presentar", exact=False).first.click(timeout=8000)
                print("[ok] Clic 1 por rol button")
                clicado = True
            except Exception as exc:
                print(f"[fallo] No se pudo hacer clic en Diligenciar: {type(exc).__name__}")

        await page.wait_for_timeout(3500)
        # El selector de formularios es una SPA Angular; esperamos a que cargue.
        try:
            await page.wait_for_selector(
                "#pre-bootstrap", state="hidden", timeout=20000
            )
        except Exception:
            pass
        await page.wait_for_timeout(4000)
        await _dump(page, dir_out, "02_diligenciar_presentar")
        await _ids(page, "btnformulario", "formulario", "210", "tiposFormulario", "pnlFormulario", "btnSelFormulario")

        # Paso 2: clic en "Formulario 210" dentro de la SPA
        try:
            unidad = page.get_by_text("Renta Personas Naturales", exact=False).first
            await unidad.click(timeout=10000)
            print("[ok] Clic en Formulario 210")
        except Exception as exc:
            print(f"[aviso] Clic 210 por texto: {type(exc).__name__}")
            try:
                await page.locator("text=Formulario 210").first.click(timeout=10000)
                print("[ok] Clic en 'Formulario 210'")
            except Exception as exc2:
                print(f"[fallo] No se pudo clicar 210: {type(exc2).__name__}")

        await page.wait_for_timeout(4500)
        await _dump(page, dir_out, "03_formulario210")
        await _ids(page, "presentadas", "declaracion", "btnPagar", "pagar", "liquida", "anio", "periodo", "buscar", "consultar")

        # Paso 3: clic en "Declaraciones de renta presentadas"
        try:
            await page.get_by_text("Declaraciones de renta presentadas", exact=True).first.click(timeout=10000)
            print("[ok] Clic en 'Declaraciones de renta presentadas'")
        except Exception as exc:
            print(f"[aviso] Clic presentadas: {type(exc).__name__}")
            try:
                await page.locator("text=Declaraciones de renta presentadas").first.click(timeout=10000)
                print("[ok] Clic por text= presentadas")
            except Exception as exc2:
                print(f"[fallo] No se pudo clicar presentadas: {type(exc2).__name__}")

        await page.wait_for_timeout(5000)
        await _dump(page, dir_out, "04_presentadas")
        await _ids(page, "tabla", "dataTable", "table", "anio", "periodo", "fila", "accion", "pagar", "buscar")

        # Paso 4: clic en el botón "Pagar" de la fila de la declaración 2025.
        # La tabla es mat-table (.mat-row). Filtramos la fila que contenga "2025 / anual"
        # y en ella el botón con icono "Pagar".
        try:
            fila = page.locator("mat-row", has_text="2025 / anual").first
            await fila.wait_for(timeout=10000)
            btn_pagar = fila.locator("button.buttonIconAccion img[matTooltip='Pagar'], button.buttonIconAccion")
            # El botón de Pagar es el tercer action button en fila (Descargar, Corregir, Pagar)
            n = await btn_pagar.count()
            print(f"[ok] Fila 2025 encontrada con {n} botones de acción")
            if n >= 3:
                await btn_pagar.nth(2).click(force=True)
            else:
                await btn_pagar.nth(n - 1).click(force=True)
            print("[ok] Clic en Pagar (fila 2025)")
        except Exception as exc:
            print(f"[aviso] Clic Pagar fila 2025: {type(exc).__name__}")
            # intento por tooltip exacto
            try:
                await page.locator("button.buttonIconAccion img[matTooltip='Pagar']").nth(1).click(force=True)
                print("[ok] Clic Pagar por tooltip (nth 1)")
            except Exception as exc2:
                print(f"[fallo] Pagar: {type(exc2).__name__}")

        await page.wait_for_timeout(4000)
        await _dump(page, dir_out, "05_modal_pagar")
        await _ids(page, "dialog", "modal", "si", "accept", "confirm", "pagar")

        # Paso 5: clic en el botón SI/YES del modal
        try:
            await page.get_by_role("button", name="SI/YES", exact=True).click(timeout=10000)
            print("[ok] Clic en SI/YES")
        except Exception as exc:
            print(f"[aviso] Clic SI/YES: {type(exc).__name__}")
            try:
                await page.locator("text=SI/YES").first.click(timeout=10000)
                print("[ok] Clic text SI/YES")
            except Exception as exc2:
                print(f"[fallo] SI/YES: {type(exc2).__name__}")

        await page.wait_for_timeout(5000)
        await _dump(page, dir_out, "06_calendario")
        await _ids(page, "calend", "calendar", "fecha", "date", "pago", "generar", "recibo", "liquida")

        print(f"\n[fin] Volcados en {dir_out}")
    finally:
        await context.close()
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verifica flujo Diligenciar y Presentar / Formulario 210")
    parser.add_argument("--cedula", required=True)
    parser.add_argument("--clave", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.cedula, args.clave))