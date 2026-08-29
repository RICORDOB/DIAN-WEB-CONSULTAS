#!/usr/bin/env bash
# =============================================================================
# actualizar_backup.sh — Refresca el snapshot ACTUAL del backup del proyecto.
#
# Cada corrida regenera (en $BACKUP_DIR/ACTUAL y raíz de backups):
#   ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.bundle   -> historia git completa (verificada)
#   ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz   -> working tree + .git (verificado)
#   CREDENCIALES.txt                          -> resaltadas API + admin (chmod 600)
#   RESTAURAR.txt                             -> nota con el commit de referencia
#
# Uso:  RENDER_API_KEY="rnd_xxx" ./scripts/actualizar_backup.sh
#   - Sin RENDER_API_KEY: actualiza bundle/tar/notas y CONSERVA las credenciales.
#     (el script NO contiene secretos).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
BK_DIR="${BACKUP_DIR:-$HOME/Documents/backups-dian}"
ACTUAL="$BK_DIR/ACTUAL"
REPO_NAME="$(basename "$REPO")"
DATE="$(date +%F)"

mkdir -p "$ACTUAL"

echo "repo:      $REPO"
echo "backup:    $BK_DIR"
echo "fecha:     $DATE"
if ! git -C "$REPO" status --porcelain | grep -q .; then
  echo "worktree:  limpio (al día con el último commit)"
else
  echo "worktree:  * HAY CAMBIOS SIN COMMITEAR (el backup cubre el último commit) *"
fi

# --- 1. Bundle de historia completa (verificado clonando) ---------------------
echo "== 1. bundle =="
git -C "$REPO" bundle create "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.bundle" --all
TMPC="$(mktemp -d)"
git clone -q "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.bundle" "$TMPC/check" \
  && echo "   bundle OK (clonado desde el respaldo)"
rm -rf "$TMPC"

# --- 2. Tar.gz del working tree (con .git, sin basura ni datos de clientes) ----
echo "== 2. tar.gz =="
tar -czf "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz" \
  -C "$(dirname "$REPO")" \
  --exclude="$REPO_NAME/.pytest_cache" \
  --exclude="*/__pycache__" \
  --exclude="*.pyc" \
  --exclude="$REPO_NAME/.DS_Store" \
  --exclude="*clientes_dian.xlsx" \
  --exclude="*cliente_individual.xlsx" \
  "$REPO_NAME"
tar -tzf "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz" >/dev/null \
  && echo "   tar OK"
if tar -tzf "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz" \
     | grep -qE '\.pyc|__pycache__|\.pytest_cache|_dian\.xlsx|\.DS_Store'; then
  echo "   AVISO: se coló contenido excluido en el tar."
fi

# --- 3. Credenciales (solo si hay API key; si no, se conservan) ----------------
echo "== 3. credenciales =="
if [[ -n "${RENDER_API_KEY:-}" ]]; then
  TMPV="$(mktemp)"
  curl -sS -m 30 -H "Authorization: Bearer $RENDER_API_KEY" \
    "https://api.render.com/v1/services/srv-d9v6jam7bikc73bt9rn0/env-vars" -o "$TMPV"
  python3 - "$TMPV" "$RENDER_API_KEY" "$DATE" > "$BK_DIR/CREDENCIALES.txt" <<'PY'
import json, sys
jf, api, fecha = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(jf))
svars = {x["envVar"]["key"]: x["envVar"]["value"] for x in data}
def g(k, d=""):
    return svars.get(k, d)
L = []
L.append("###############################################################")
L.append("#  DIAN-WEB-CONSULTAS - CREDENCIALES DE DESPLIEGUE            #")
L.append("#  Ultima actualizacion: %s                                #" % fecha)
L.append("#  PRIVADO: no compartir. Mantener permisos 600.              #")
L.append("###############################################################")
L.append("")
L.append("################################################################")
L.append("#  *  API DE RENDER  *                                        #")
L.append("#  >>>>>> GUARDA ESTE VALOR <<<<<<                            #")
L.append("################################################################")
L.append("  RENDER_API_KEY = %s" % api)
L.append("")
L.append("################################################################")
L.append("#  *  CREDENCIALES DEL ADMINISTRADOR (login web)  *           #")
L.append("#  >>>>>> USUARIO Y CLAVE PARA ENTRAR EN LA APP <<<<<<        #")
L.append("################################################################")
L.append("  USUARIO  = %s" % g("APP_ADMIN_USER", "ricardo"))
L.append("  CLAVE    = %s" % g("APP_ADMIN_PASS"))
L.append("")
L.append("################################################################")
L.append("#  OTRAS VARIABLES DEL SERVICIO (dian-web)                    #")
L.append("#  servicio: srv-d9v6jam7bikc73bt9rn0  https://dian-web.onrender.com")
L.append("################################################################")
L.append("  APP_ADMIN_USER = %s" % g("APP_ADMIN_USER", "ricardo"))
L.append("  APP_ADMIN_PASS = %s" % g("APP_ADMIN_PASS"))
L.append("  APP_SECRET_KEY = %s" % g("APP_SECRET_KEY"))
L.append("  APP_DATA_DIR   = %s" % g("APP_DATA_DIR", "/data"))
L.append("  APP_JOBS_DIR   = %s" % g("APP_JOBS_DIR", "/data/jobs"))
if svars.get("TURSO_DB_URL") or svars.get("TURSO_AUTH_TOKEN"):
    L.append("  TURSO_DB_URL    = %s" % g("TURSO_DB_URL"))
    L.append("  TURSO_AUTH_TOKEN= %s" % g("TURSO_AUTH_TOKEN"))
L.append("")
L.append("################################################################")
L.append("#  NOTAS                                                       #")
L.append("################################################################")
L.append("# - Repo: https://github.com/RICORDOB/DIAN-WEB-CONSULTAS (main)")
L.append("# - El repositorio no contiene secretos; se restauran desde este archivo.")
print("\n".join(L))
PY
  chmod 600 "$BK_DIR/CREDENCIALES.txt"
  rm -f "$TMPV"
  echo "   CREDENCIALES.txt regenerado desde el servicio (permisos 600)"
else
  echo "   Sin RENDER_API_KEY: se conserva CREDENCIALES.txt actual."
fi

# --- 4. Notas de restauración con el commit de referencia ----------------------
echo "== 4. notas de restauración =="
COMMIT="$(git -C "$REPO" log --oneline -1)"
cat > "$BK_DIR/RESTAURAR.txt" <<EOF
DIAN-WEB-CONSULTAS — Guía de restauración (backup ACTUAL)

Snapshot ACTUAL (ultima actualizacion: $DATE)
Commit de referencia: $COMMIT

Backup disponible:
  - ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.bundle  (git bundle: todo el repo + historia)
  - ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz  (working tree + .git, sin datos de clientes)

== 1. Restaurar el codigo (git bundle) ==
  git clone ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.bundle <carpeta-destino>
  cd <carpeta-destino>
  git remote set-url origin https://github.com/RICORDOB/DIAN-WEB-CONSULTAS.git

== 2. Alternativa: descomprimir el tar.gz ==
  tar -xzf ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz -C <carpeta-destino>

== 3. Entorno ==
  python3 -m venv venv && source venv/bin/activate
  pip install -r requirements.txt
  pip install -r requirements-dev.txt

== 4. Credenciales ==
  Ver CREDENCIALES.txt (resaltadas API de Render y usuario/clave del admin).

== 5. Despliegue en Render ==
  Dashboard: https://dashboard.render.com/web/srv-d9v6jam7bikc73bt9rn0
  o por API: ./deploy_render.sh   (con RENDER_API_KEY exportada)
  o CI: cualquier push a main despliega automaticamente (GitHub Action).

== 6. Pruebas ==
  pytest
EOF
echo "   RESTAURAR.txt actualizado (commit: $COMMIT)"

# --- 5. Resumen / checksums ------------------------------------------------
echo "== 5. resumen =="
shasum "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.bundle" "$ACTUAL/DIAN-WEB-CONSULTAS-ACTUAL.tar.gz"
echo "== backup ACTUAL listo =="