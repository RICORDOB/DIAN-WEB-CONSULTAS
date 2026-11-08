#!/usr/bin/env python3
"""Convierte la respuesta de la API de Render (/env-vars) a un archivo .env.

Uso:
    python3 scripts/env_render_a_dotenv.py /tmp/env-render.json .env

- Nunca imprime los valores: solo los nombres de las variables exportadas.
- Fuerza APP_DATA_DIR/APP_JOBS_DIR a las rutas del contenedor.
- Deja el archivo con permisos 600.
"""

from __future__ import annotations

import json
import os
import shlex
import sys

# Variables que gestiona el contenedor, no Render.
OVERRIDES = {
    "APP_DATA_DIR": "/data",
    "APP_JOBS_DIR": "/data/jobs",
    "PORT": "8000",
}

# Claves críticas que deben conservarse para no perder datos cifrados.
CRITICAS = ("APP_SECRET_KEY", "TURSO_DB_URL", "TURSO_AUTH_TOKEN",
            "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    origen, destino = sys.argv[1], sys.argv[2]
    with open(origen, encoding="utf-8") as fh:
        datos = json.load(fh)

    # La API puede devolver una lista plana o un objeto con "envVars".
    if isinstance(datos, dict):
        datos = datos.get("envVars", datos.get("env_vars", []))

    variables: dict[str, str] = {}
    for item in datos:
        if not isinstance(item, dict):
            continue
        # La API v1 envuelve cada variable en {"envVar": {...}, "cursor": ...}
        if isinstance(item.get("envVar"), dict):
            item = item["envVar"]
        clave = item.get("key") or item.get("name")
        valor = item.get("value")
        if not clave:
            continue
        variables[str(clave)] = "" if valor is None else str(valor)

    variables.update(OVERRIDES)

    lineas = [
        "# Generado desde Render — NO subir al repositorio.",
        "# Conserva APP_SECRET_KEY: cambiarla invalida el cifrado del catálogo.",
        "",
    ]
    for clave in sorted(variables):
        lineas.append(f"{clave}={shlex.quote(variables[clave])}")
    lineas.append("")

    with open(destino, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas))
    os.chmod(destino, 0o600)

    print(f"OK: {len(variables)} variables escritas en {destino} (chmod 600)")
    for clave in CRITICAS:
        estado = "presente" if variables.get(clave) else "AUSENTE"
        print(f"  {clave}: {estado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
