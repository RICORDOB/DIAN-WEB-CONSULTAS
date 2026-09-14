"""
Sondeo de páginas: tras clic en Pagar y SI/YES, lista todas las páginas del
contexto de Playwright y vuelca la que muestre calendario/generar recibo.
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
    try:
        html = await page.content()
        (dir_out / f"{nombre}.html").write_text(html, encoding="utf-8")
        await page.screenshot(path=str(dir_out / f"{nombre}.png"), full_page=True)
        print(f"[volcado] {nombre}.html | {nombre}.png")
    except Exception as exc:
        print(f"[aviso] dump {nombre}: {type(exc).__name__}: {exc}")


async def _titulo_y_texto(page) -> str:
    try:
        t = await page.title()
        body = await page.inner_text("body")
        return f"title={t!r} :: {body[:120]!r}"
    except Exception as exc:
        return f"err {type(exc).__name__}"


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
        async with context.expect_page(timeout=15000) as pe:
            try:
                await page.locator("input[id*='btnDiligenciarPresentar']").first.click(force=True, timeout=8000)
            except Exception:
                await page.locator("input[id*='btnDiligenciarYpresentar']").first.click(force=True, timeout=8000)
            await page.wait_for_timeout(3000)
        nueva = await pe.value
        print("[ok] Nueva página:", await _titulo_y_texto(nueva))
        dir_out2 = dir_out / "popup_sp"
        dir_out2.mkdir(exist_ok=True)
        await _dump(nueva, dir_out2, "00_spa")
    except Exception as exc:
        print(f"[aviso] ExpectPage: {type(exc).__name__}")
        nueva = page  # no popup: seguimos con la página principal

    if nueva is not page:
        page = nueva
        try:
            await page.wait_for_selector("#pre-bootstrap", state="hidden", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        await _dump(page, dir_out, "01_spa_cargada")

    # Clic 210
    try:
        await page.get_by_text("Renta Personas Naturales", exact=False).first.click(timeout=10000)
        print("[ok] Clic Formulario 210")
    except Exception as exc:
        print(f"[aviso] 210: {type(exc).__name__}")
    await page.wait_for_timeout(4000)
    await _dump(page, dir_out, "02_formulario210")

    # Clic presentadas
    try:
        await page.get_by_text("Declaraciones de renta presentadas", exact=True).first.click(timeout=10000)
        print("[ok] Clic presentadas")
    except Exception as exc:
        print(f"[aviso] presentadas: {type(exc).__name__}")
    await page.wait_for_timeout(5000)
    await _dump(page, dir_out, "03_presentadas")

    # Clic Pagar fila 2025
    try:
        fila = page.locator("mat-row", has_text="2025 / anual").first
        await fila.wait_for(timeout=10000)
        btns = fila.locator("button.buttonIconAccion")
        n = await btns.count()
        await btns.nth(n - 1).click(force=True)
        print(f"[ok] Clic Pagar fila 2025 (btn index {n-1})")
    except Exception as exc:
        print(f"[aviso] pagar: {type(exc).__name__}")
    await page.wait_for_timeout(4000)
    await _dump(page, dir_out, "04_modal_pagar")
    print("PÁGINAS TRAS PAGAR:", [await _titulo_y_texto(pg) for pg in context.pages])

    # Clic SI/YES
    try:
        await page.get_by_role("button", name="SI/YES", exact=True).click(timeout=10000)
        print("[ok] Clic SI/YES")
    except Exception as exc:
        print(f"[aviso] SI/YES: {type(exc).__name__}")
    await page.wait_for_timeout(6000)
    print("PÁGINAS TRAS SI:", [await _titulo_y_texto(pg) for pg in context.pages])
    for idx, pg in enumerate(context.pages):
        await _dump(pg, dir_out, f"05_tras_si_pag{idx}")

    print(f"\n[fin] Volcados en {dir_out}")
    await context.close()
    await browser.close()
    await p.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sondea páginas del flujo de pago/recibo 210")
    parser.add_argument("--cedula", required=True)
    parser.add_argument("--clave", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.cedula, args.clave))