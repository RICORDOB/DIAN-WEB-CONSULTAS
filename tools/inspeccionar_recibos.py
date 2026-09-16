"""
Fase 2 de descubrimiento: tras login, hace clic en "Sus recibos de pago"
(input[id='vistaDashboard:frmDashboard:btnPagoRecibos']) y explora el módulo
de consulta de recibos/declaraciones. Vuelca HTML + capturas en cada paso.
"""

from __future__ import annotations

import argparse
import asyncio
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


async def _selects(page) -> list[str]:
    """Lista los <select> con id, opciones y valor actual."""
    out = []
    try:
        for sel in page.locator("select"):
            try:
                sid = await sel.get_attribute("id")
                raw = await sel.evaluate("(el) => Array.from(el.options).map(o => o.text).join(' | ')")
                val = await sel.input_value()
                out.append(f"{sid} | actual={val} | {raw[:160]}")
            except Exception:
                pass
    except Exception:
        pass
    return out


async def _inputs(page) -> list[str]:
    """Lista los inputs submit/image con id y nombre."""
    out = []
    try:
        for it in page.locator("input[type='submit'], input[type='button'], input[type='image'], button"):
            try:
                iid = await it.get_attribute("id")
                name = await it.get_attribute("name")
                src = await it.get_attribute("src")
                txt = (await it.inner_text()).strip()[:40]
                if iid and ("btn" in iid.lower() or "buscar" in iid.lower() or "consul" in iid.lower()):
                    out.append(f"{iid} | src={src.split('/')[-1] if src else ''} | txt={txt}")
            except Exception:
                pass
    except Exception:
        pass
    return out


async def _tablas(page) -> list[dict]:
    """Detecta tablas con recibos/declaraciones (id y primeras celdas)."""
    tablas = []
    try:
        for tb in page.locator("table"):
            try:
                tid = await tb.get_attribute("id")
                if not tid:
                    continue
                celdas = (await tb.inner_text()).strip().replace("\n", " | ")[:200]
                if "recibo" in celdas.lower() or "declarac" in celdas.lower() or "fecha" in celdas.lower():
                    tablas.append({"id": tid, "contenido": celdas})
            except Exception:
                pass
    except Exception:
        pass
    return tablas


async def _cli(page, dir_out: Path, creds: dict) -> None:
    await page.wait_for_timeout(2500)
    await _dump(page, dir_out, "01_dashboard")

    btn = page.locator("input[id='vistaDashboard:frmDashboard:btnPagoRecibos']")
    try:
        await btn.first.click(timeout=10000, force=True)
        print("[ok] Clic en btnPagoRecibos")
    except Exception as exc:
        print(f"[fallo] btnPagoRecibos: {type(exc).__name__}: {exc}")
        await _dump(page, dir_out, "02_error")

    await page.wait_for_timeout(3500)
    await _dump(page, dir_out, "02_recibos")

    print("\n[foreach] <select> visibles:")
    for s in await _selects(page):
        print("  -", s)

    print("\n[foreach] botones/inputs:")
    for i in await _inputs(page):
        print("  -", i)

    print("\n[foreach] tablas con recibos/declaraciones:")
    for t in await _tablas(page):
        print(f"  - id={t['id']} :: {t['contenido']}")


async def main(cedula: str, clave: str) -> None:
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
        await _cli(page, dir_out, creds)
        print(f"\n[fin] Volcados en {dir_out}")
    finally:
        await context.close()
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explora el módulo de recibos de pago de MUISCA")
    parser.add_argument("--cedula", required=True)
    parser.add_argument("--clave", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.cedula, args.clave))