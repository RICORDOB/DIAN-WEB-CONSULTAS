#!/usr/bin/env bash
# =============================================================================
# Despliegue de ExoRenta (DIAN-WEB-CONSULTAS) en Google Cloud Run.
#
# Escala a cero: $0 en reposo (casi todo el año). En temporada de masivo
# (julio-octubre) se sube MIN_INSTANCES a 1 para que el lote en segundo plano
# no se duerma; el cómputo de esos meses lo financia el servicio de pago.
#
# Uso (desde la raíz del repo, con gcloud autenticado y proyecto elegido):
#   ./scripts/deploy_cloudrun.sh
#
# Variables de entorno opcionales (todas tienen default razonable):
#   GCLOUD_PROJECT  id del proyecto (si no, usa el activo de gcloud)
#   REGION          región (default us-east1)
#   MIN_INSTANCES   0 = $0 en reposo | 1 = caliente (temporada de masivo)
#   MEMORY / CPU / TIMEOUT  recursos del contenedor
#
# La imagen se construye en Google Cloud Build (amd64), no en la Mac (M1),
# para no generar un binario arm64 incompatible con Cloud Run.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configurables
# ---------------------------------------------------------------------------
PROJECT="${GCLOUD_PROJECT:-}"
REGION="${REGION:-us-east1}"
SERVICE="${SERVICE:-exorenta}"
REPO_ARTIFACT="${REPO_ARTIFACT:-exorenta}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MEMORY="${MEMORY:-4Gi}"
CPU="${CPU:-2}"
TIMEOUT="${TIMEOUT:-900}"   # 15 min: la consulta DIAN puede tardar varios minutos

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
ENV_YAML="$REPO_DIR/cloudrun.env.yaml"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud no está instalado. 'brew install google-cloud-sdk'." >&2
  exit 1
}
if [ -z "$PROJECT" ]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [ -z "$PROJECT" ]; then
  echo "ERROR: define GCLOUD_PROJECT o ejecuta 'gcloud config set project <id>'." >&2
  exit 1
fi
[ -f "$ENV_FILE" ] || {
  echo "ERROR: falta $ENV_FILE (ver .env.example). Copia tus secretos primero." >&2
  exit 1
}

echo "==> 1/5 Proyecto $PROJECT (región $REGION)"

# ---------------------------------------------------------------------------
# 2. APIs y repositorio de imágenes (idempotente)
# ---------------------------------------------------------------------------
echo "==> 2/5 Activando APIs (Cloud Run, Artifact Registry, Cloud Build)"
gcloud services enable --project "$PROJECT" \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

gcloud artifacts repositories create "$REPO_ARTIFACT" \
  --repository-format=docker --location="$REGION" --project "$PROJECT" \
  >/dev/null 2>&1 || true   # ya existe -> OK

# SHA del commit: etiqueta la imagen y se expone en /health como APP_VERSION.
SHA="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo manual)"

# ---------------------------------------------------------------------------
# 3. Variables de entorno para Cloud Run (desde .env, sin exponer secretos)
# ---------------------------------------------------------------------------
echo "==> 3/5 Preparando variables de entorno"
python3 - "$ENV_FILE" "$ENV_YAML" "$SHA" <<'PY'
import pathlib, sys

env_file, out_file, sha = sys.argv[1], sys.argv[2], sys.argv[3]

# Solo migramos las variables de la app. Cloud Run inyecta su propio PORT y
# forzamos /tmp (el filesystem del contenedor es de solo lectura salvo /tmp;
# la persistencia real vive en Turso, así que /tmp solo guarda jobs efímeros).
CLAVES = ["APP_SECRET_KEY", "APP_ADMIN_USER", "APP_ADMIN_PASS",
          "TURSO_DB_URL", "TURSO_AUTH_TOKEN"]

valores = {}
for linea in pathlib.Path(env_file).read_text(encoding="utf-8").splitlines():
    linea = linea.strip()
    if not linea or linea.startswith("#") or "=" not in linea:
        continue
    clave, _, valor = linea.partition("=")
    clave = clave.strip()
    if clave in CLAVES and clave not in valores:
        valores[clave] = valor.strip().strip('"').strip("'")

faltan = [c for c in CLAVES if c not in valores]
if faltan:
    print(f"ERROR: faltan en .env: {', '.join(faltan)}", file=sys.stderr)
    sys.exit(1)

# Overrides fijos de Cloud Run (no tomamos APP_DATA_DIR/APP_JOBS_DIR/PORT del .env)
valores["APP_ENV"] = "prod"
valores["APP_DATA_DIR"] = "/tmp/data"
valores["APP_JOBS_DIR"] = "/tmp/jobs"
valores["APP_VERSION"] = sha

def yaml(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"

lineas = [f"{k}: {yaml(v)}" for k, v in sorted(valores.items())]
pathlib.Path(out_file).write_text("\n".join(lineas) + "\n", encoding="utf-8")
print(f"    {out_file}  ({len(lineas)} variables)")
PY

# ---------------------------------------------------------------------------
# 4. Build en Cloud Build (amd64) y push al registro
# ---------------------------------------------------------------------------
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO_ARTIFACT/exorenta:$SHA"

echo "==> 4/5 Construyendo imagen $IMAGE (Cloud Build, amd64)"
gcloud builds submit "$REPO_DIR" \
  --project "$PROJECT" --region "$REGION" \
  --tag "$IMAGE" --timeout=1800s \
  --quiet

# ---------------------------------------------------------------------------
# 5. Deploy a Cloud Run
# ---------------------------------------------------------------------------
echo "==> 5/5 Desplegando $SERVICE (min-instances=$MIN_INSTANCES)"
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" --platform managed \
  --image "$IMAGE" \
  --max-instances 1 --concurrency 1 \
  --memory "$MEMORY" --cpu "$CPU" \
  --timeout "$TIMEOUT" --min-instances "$MIN_INSTANCES" \
  --port 8080 \
  --allow-unauthenticated \
  --env-vars-file "$ENV_YAML" \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" \
  --region "$REGION" --format='value(status.url)')"

echo
echo "Desplegado. URL del servicio: $URL"
echo "Health: curl -s $URL/health"
echo "Panel admin: $URL/dev"
echo
echo "En temporada de masivo, sube a 1 instancia caliente con:"
echo "  MIN_INSTANCES=1 ./scripts/deploy_cloudrun.sh"
