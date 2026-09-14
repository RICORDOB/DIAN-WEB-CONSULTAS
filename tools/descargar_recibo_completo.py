"""
Flujo completo de descarga de recibo de pago (490) — verificación real.

login → Diligenciar y presentar → Formulario 210 → Declaraciones de renta
presentadas → Pagar (fila del año) → SI/YES → Fecha de pago → Generar recibo
→ expect_download → save_as PDF.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runner import DianRunner  # noqa: E402
from app.comun import TIMEOUT_DESCARGA  # noqa: E402


def _dir_trabajo() -> Path:
    base = Path(".inspeccion_flujo")
    base.mkdir(exist_ok=True)
    return base / datetime.now().strftime("%Y%m%d_%H%M%S")


async def _dump(page, dir_out: Path, nombre: str):
    try:
        (dir_out / f"{nombre}.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(dir_out / f"{nombre}.png"), full_page=True)
        print(f"[volcado] {nombre}")
    except Exception as exc:
        print(f"[aviso] dump {nombre}: {type(exc).__name__}")


async def main(cedula: str, clave: str, anio: str, fecha_pago: str) -> None:
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
        # 0) Diligenciar y presentar
        await page.wait_for_timeout(2500)
        try:
            await page.locator("input[id*='btnDiligenciarPresentar']").first.click(force=True, timeout=10000)
            print("[ok] 1. Diligenciar y presentar")
        except Exception as exc:
            print(f"[aviso] 1: {type(exc).__name__}")
        try:
            await page.wait_for_selector("#pre-bootstrap", state="hidden", timeout=20000)
        except Exception:
            pass
        # Espera el listado de formularios de la SPA
        try:
            await page.get_by_text("Renta Personas Naturales", exact=False).first.wait_for(timeout=20000)
        except Exception as exc:
            print(f"[aviso] SPA no cargó a tiempo ({type(exc).__name__}); dump diagnóstico")
            await _dump(page, dir_out, "00_estado_tras_clic_dilig")
        await page.wait_for_timeout(3500)

        # 1) Formulario 210
        await page.get_by_text("Renta Personas Naturales", exact=False).first.click(timeout=10000)
        print("[ok] 2. Formulario 210")
        await page.wait_for_timeout(4500)

        # 2) Declaraciones de renta presentadas
        await page.get_by_text("Declaraciones de renta presentadas", exact=True).first.click(timeout=10000)
        print("[ok] 3. Declaraciones de renta presentadas")
        await page.wait_for_timeout(5000)

        # 3) Pagar (fila del año)
        fila = page.locator("mat-row", has_text=f"{anio} / anual").first
        await fila.wait_for(timeout=10000)
        btns = fila.locator("button.buttonIconAccion")
        n = await btns.count()
        if n >= 3:
            await btns.nth(n - 1).click(force=True)
        else:
            await btns.nth(n - 1).click(force=True)
        print(f"[ok] 4. Pagar fila {anio}")
        await page.wait_for_timeout(4000)
        await _dump(page, dir_out, "04_modal_pagar")

        # 4) SI/YES
        await page.get_by_role("button", name="SI/YES", exact=True).click(timeout=10000)
        print("[ok] 5. SI/YES")
        await page.wait_for_timeout(6000)
        await _dump(page, dir_out, "05_datos_pago")

        # 5) Fecha de pago
        campo = page.locator("input[type='date']").first
        await campo.fill(fecha_pago)
        valor = await campo.input_value()
        print(f"[ok] 6. Fecha de pago -> {valor}")
        await page.wait_for_timeout(500)

        # 6) Generar recibo -> luego aparece "Descargar recibo de pago"
        try:
            await page.get_by_role("button", name="Generar recibo", exact=True).click(force=True, timeout=10000)
            print("[ok] 7. Generar recibo")
        except Exception as exc:
            print(f"[aviso] clic Generar recibo: {type(exc).__name__}")
        await page.wait_for_timeout(5000)

        # 7) Descargar recibo de pago (el PDF)
        destino = dir_out / f"recibo_{cedula}_{fecha_pago}.pdf"
        try:
            btn_desc = page.get_by_role("button", name="Descargar recibo de pago", exact=True)
            await btn_desc.wait_for(timeout=15000)
            async with page.expect_download(timeout=TIMEOUT_DESCARGA) as dl_info:
                await btn_desc.click(force=True)
                download = await dl_info.value
            await download.save_as(destino)
            print(f"[ok] 8. Descarga recibo: {destino} ({destino.stat().st_size} bytes)")
            print(f"[ok] Nombre original: {download.suggested_filename}")
        except Exception as exc:
            print(f"[aviso] descarga: {type(exc).__name__}: {exc}")
            await _dump(page, dir_out, "06_tras_generar")

        print(f"\n[fin] Volcados en {dir_out}")
    finally:
        await context.close()
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Descarga recibo de pago 210")
    parser.add_argument("--cedula", required=True)
    parser.add_argument("--clave", required=True)
    parser.add_argument("--anio", default="2025")
    parser.add_argument("--fecha", default="2026-09-30")
    args = parser.parse_args()
    asyncio.run(main(args.cedula, args.clave, args.anio, args.fecha))