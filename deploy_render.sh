#!/usr/bin/env bash
# =============================================================================
# Deploy automatizado de DIAN-WEB-CONSULTAS en Render via API
#
# Uso:
#   RENDER_API_KEY="rnd_xxx" ./deploy_render.sh
#
# Requisitos:
#   - API key de Render: https://dashboard.render.com/account/api-keys
#   - Repo privado DIAN-WEB-CONSULTAS ya visible para Render (ver paso anterior)
# =============================================================================
set -euo pipefail

API="${RENDER_API_KEY:-${RENDER_API_KEY:-}}"
if [[ -z "$API" ]]; then
  echo "ERROR: Define RENDER_API_KEY (https://dashboard.render.com/account/api-keys)" >&2
  exit 1
fi

# Configuracion editable
REPO="https://github.com/RICORDOB/DIAN-WEB-CONSULTAS"
BRANCH="main"
SERVICE_NAME="dian-web"
PLAN="starter"
REGION="oregon"
ADMIN_USER="${APP_ADMIN_USER:-ricardo}"
ADMIN_PASS="${APP_ADMIN_PASS:-}"
# SECRET_KEY aleatorio por despliegue (NUNCA uno fijo/commiteado)
SECRET_KEY="${APP_SECRET_KEY:-$(openssl rand -hex 32)}"

if [[ -z "$ADMIN_PASS" ]]; then
  echo "ERROR: Define APP_ADMIN_PASS (contraseña del administrador)" >&2
  exit 1
fi

AUTH="Authorization: Bearer $API"
CT="Content-Type: application/json"

echo "==> 1. Obteniendo workspace (ownerId)..."
OWNERS="$(curl -s -H "$AUTH" https://api.render.com/v1/owners)"
OWNER_ID="$(echo "$OWNERS" | python3 -c 'import sys,json; d=json.load(sys.stdin); o=d[0]["owner"] if d else {}; print(o.get("id",""))')"
if [[ -z "$OWNER_ID" ]]; then
  echo "ERROR: No se pudo obtener ownerId. Respuesta:" >&2
  echo "$OWNERS" >&2
  exit 1
fi
echo "    ownerId=$OWNER_ID"

echo "==> 2. Creando Web Service (Docker, $PLAN)..."
PAYLOAD=$(cat <<JSON
{
  "type": "web_service",
  "name": "$SERVICE_NAME",
  "ownerId": "$OWNER_ID",
  "repo": "$REPO",
  "branch": "$BRANCH",
  "autoDeploy": "yes",
  "serviceDetails": {
    "runtime": "docker",
    "plan": "$PLAN",
    "region": "$REGION",
    "numInstances": 1,
    "envSpecificDetails": {
      "dockerCommand": "",
      "dockerfilePath": "./Dockerfile",
      "dockerContext": "."
    },
    "healthCheckPath": "/"
  },
  "envVars": [
    {"key": "APP_ADMIN_USER", "value": "$ADMIN_USER"},
    {"key": "APP_ADMIN_PASS", "value": "$ADMIN_PASS"},
    {"key": "APP_SECRET_KEY", "value": "$SECRET_KEY"},
    {"key": "APP_DATA_DIR", "value": "/data"},
    {"key": "APP_JOBS_DIR", "value": "/data/jobs"}
  ]
}
JSON
)

CREATE="$(curl -s -X POST -H "$AUTH" -H "$CT" -d "$PAYLOAD" https://api.render.com/v1/services)"
SERVICE_ID="$(echo "$CREATE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("service",{}).get("id","") if isinstance(d,dict) else "")')"
if [[ -z "$SERVICE_ID" ]]; then
  echo "ERROR al crear el servicio. Respuesta:" >&2
  echo "$CREATE" >&2
  exit 1
fi
echo "    service_id=$SERVICE_ID"

echo "==> 3. Esperando el primer deploy (puede tardar varios minutos)..."
echo "    Dashboard: https://dashboard.render.com/web/srv-$SERVICE_ID"
for i in $(seq 1 90); do
  DEPLOYS="$(curl -s -H "$AUTH" "https://api.render.com/v1/services/$SERVICE_ID/deploys")"
  STATUS="$(echo "$DEPLOYS" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["status"] if d else "none")' 2>/dev/null || echo none)"
  echo "    [$i] estado: $STATUS"
  if [[ "$STATUS" == "live" ]]; then
    URL="$(curl -s -H "$AUTH" "https://api.render.com/v1/services/$SERVICE_ID" | python3 -c 'import sys,json; print(json.load(sys.stdin)["serviceDetails"].get("url",""))' 2>/dev/null || true)"
    echo ""
    echo "=============================================="
    echo "  DEPLOY COMPLETADO"
    echo "  URL: $URL"
    echo "  Dashboard: https://dashboard.render.com/web/srv-$SERVICE_ID"
    echo "=============================================="
    exit 0
  fi
  if [[ "$STATUS" == "build_failed" || "$STATUS" == "deploy_failed" || "$STATUS" == "canceled" ]]; then
    echo "ERROR: el deploy terminó en estado '$STATUS'. Revisa los logs:" >&2
    echo "https://dashboard.render.com/web/srv-$SERVICE_ID" >&2
    echo "$DEPLOYS" >&2
    exit 1
  fi
  sleep 20
done
echo "AVISO: el deploy sigue en curso. Revisa https://dashboard.render.com/web/srv-$SERVICE_ID" >&2
