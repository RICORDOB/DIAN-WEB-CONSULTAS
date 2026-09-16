"""
inspeccionar_declaraciones — Volcado exploratorio del módulo
"Declaraciones y Recibos de Pago" de MUISCA (portal DIAN).

Objetivo: descubrir los selectores reales para automatizar la descarga de
declaraciones (210/300/350) y recibos de pago (formulario 490) con su fecha
de pago.

Para ejecutar (desde la raíz del repo):
  source .venv/bin/activate
  python tools/inspeccionar_declaraciones.py --cedula 1045498915 --clave 'Ricardo123*'

El script NO descarga nada todavía: vuelca HTML + screenshots en una carpeta
de trabajo (./.inspeccion_declaraciones) para que podamos elegir los
selectores con evidencia del portal real.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runner import DianRunner  # noqa: E402
from app.comun import URL_LOGIN  # noqa: E402


def _dir_trabajo() -> Path:
    base = Path(".inspeccion_declaraciones")
    base.mkdir(exist_ok=True)
    return base / datetime.now().strftime("%Y%m%d_%H%M%S")


async def _dump(page, dir_out: Path, nombre: str):
    """Vuelca HTML y captura de pantalla de la página actual."""
    html_path = dir_out / f"{nombre}.html"
    png_path = dir_out / f"{nombre}.png"
    html = await page.content()
    html_path.write_text(html, encoding="utf-8")
    await page.screenshot(path=str(png_path), full_page=True)
    print(f"[volcado] {html_path}")
    print(f"[volcado] {png_path}")


async def _enlaces_y_botones(page) -> list[str]:
    """Lista de los textos visibles clickeables (para detectar el módulo)."""
    textos: list[str] = []
    for sel in ("a", "button", "input[type='submit']", "input[type='button']"):
        try:
            loc = page.locator(sel)
            n = await loc.count()
            for i in range(min(n, 60)):
                try:
                    txt = (await loc.nth(i).inner_text(timeout=1000)).strip()
                    if txt:
                        textos.append(txt[:80])
                except Exception:
                    pass
        except Exception:
            pass
    return textos


async def _modulos_dashboard(page) -> list[str]:
    """Identifica los módulos disponibles en el dashboard tras el login."""
    dashboard = page.locator("form[id='vistaDashboard:frmDashboard']")
    modulos: list[str] = []
    try:
        n = await dashboard.count()
        if n:
            html = await dashboard.first.inner_html()
            for m in html.split("id='btn")[1:]:
                modulos.append(m.split("'")[0])
    except Exception:
        pass
    return modulos


async def _buscador_por_texto(page) -> list[tuple[str, str]]:
    """(selector, texto) para localizar 'Declaraciones y Recibos de Pago'."""
    candidatos: list[tuple[str, str]] = []
    for texto in ("Declaraciones", "Recibos", "Recaudo", "declaración",
                  "recibos de pago", "Pagos", "pago"):
        try:
            loc = page.get_by_text(texto, exact=False)
            n = await loc.count()
            for i in range(min(n, 10)):
                try:
                    candidatos.append((f"text={texto} #{i}", (await loc.nth(i).inner_text()).strip()[:100]))
                except Exception:
                    pass
        except Exception:
            pass
    return candidatos


async def _explorar(page, dir_out: Path, creds: dict) -> None:
    """Tras el login, recorre el dashboard y el módulo de declaraciones."""
    await page.wait_for_timeout(2500)
    await _dump(page, dir_out, "01_dashboard_tras_login")

    modulos = await _modulos_dashboard(page)
    print("\n[foreach] Módulos detectados en el dashboard:")
    for m in modulos:
        print(f"  - btn{m}")

    candidatos = await _buscador_por_texto(page)
    print("\n[foreach] Textos que contienen 'declarac/recibos/recaudo/pago':")
    for sel, txt in candidatos[:20]:
        print(f"  - {sel}: {txt}")

    # Busca el botón/link del módulo. Preferimos por texto exacto visible.
    texto_modulo = "Declaraciones y Recibos de Pago"
    encontrado = False
    for sel, nombre in (
        (f"a:has-text('{texto_modulo}')", "link"),
        (f"button:has-text('{texto_modulo}')", "boton"),
        ("input[id*='btnDeclaraciones']", "btnDeclaraciones"),
        ("input[id*='Declaraciones']", "id-contiene-declaraciones"),
        ("text=Declaraciones y Recibos de Pago", "texto-exacto"),
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=8000)
                encontrado = True
                print(f"\n[ok] Clic en '{sel}'")
                break
        except Exception as exc:
            print(f"[aviso] '{sel}' no sirvió: {type(exc).__name__}")

    if not encontrado:
        # Último intento: clic en cualquier elemento que contenga "Recibos de Pago"
        try:
            await page.get_by_text("Recibos de Pago", exact=False).first.click(timeout=8000)
            encontrado = True
        except Exception:
            pass

    if not encontrado:
        print("\n[fallo] No se encontró el módulo. Se dejan las capturas del dashboard.")

    await page.wait_for_timeout(2500)
    await _dump(page, dir_out, "02_modulo_declaraciones")

    # Formulario de consulta: elección de formulario y año
    selectores = [
        "select[id*='formulario']",
        "select[id*='selectedFormulario']",
        "select[id*='tipoFormulario']",
        "select[id*='ano']",
        "select[id*='anio']",
        "select[id*='periodo']",
        "input[id*='btnConsultar']",
        "input[id*='btnBuscar']",
        "input[id*='btnBuscarDeclaraciones']",
        "input[value='Consultar']",
    ]
    print("\n[foreach] Selectores candidatos en el módulo:")
    for sel in selectores:
        try:
            loc = page.locator(sel)
            n = await loc.count()
            if n:
                opts = ""
                if "select" in sel:
                    try:
                        opts = await loc.first.evaluate("(el) => Array.from(el.options).map(o => o.text).join(' | ')")
                    except Exception:
                        pass
                print(f"  - {sel}: {n} encuentro(s) {opts[:200] if opts else ''}")
        except Exception:
            pass


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
        await _explorar(page, dir_out, creds)
        print(f"\n[fin] Volcados en {dir_out}")
    finally:
        await context.close()
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explora el módulo de declaraciones de MUISCA")
    parser.add_argument("--cedula", required=True)
    parser.add_argument("--clave", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.cedula, args.clave))