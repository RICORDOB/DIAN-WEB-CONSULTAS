#!/usr/bin/env bash
# =============================================================================
# Bootstrap de ExoRenta (DIAN-WEB-CONSULTAS) en un VPS Ubuntu 24.04 ARM64.
#
# Uso (en la VM de Oracle, como usuario con sudo):
#   DOMAIN=exorenta.duckdns.org ./scripts/deploy_oracle.sh
#
# Requisitos previos:
#   1. DNS del dominio apuntando a la IP pública de la VM.
#   2. Archivo .env creado en la raíz del repo (ver .env.example).
#   3. Puertos 80/443 abiertos en la Security List de Oracle.
#
# Idempotente: se puede volver a correr para actualizar el despliegue.
# =============================================================================
set -euo pipefail

DOMAIN="${DOMAIN:-}"
REPO="${REPO:-https://github.com/RICORDOB/DIAN-WEB-CONSULTAS.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/exorenta}"

if [[ $EUID -ne 0 ]]; then
  exec sudo --preserve-env=DOMAIN,REPO,BRANCH,APP_DIR bash "$0" "$@"
fi

if [[ -z "$DOMAIN" ]]; then
  echo "ERROR: define DOMAIN (ej: DOMAIN=exorenta.duckdns.org $0)" >&2
  exit 1
fi

echo "==> 1/7 Paquetes base"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 git ufw caddy curl ca-certificates

echo "==> 2/7 Firewall (solo SSH, 80 y 443)"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable --now docker >/dev/null

echo "==> 3/7 Código en $APP_DIR"
mkdir -p "$APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --prune origin
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" --depth 1 "$REPO" "$APP_DIR"
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "ERROR: falta $APP_DIR/.env. Cópialo de .env.example y completa los secretos." >&2
  exit 1
fi
chmod 600 "$APP_DIR/.env"

echo "==> 4/7 Caddy con dominio $DOMAIN"
install -m 644 "$APP_DIR/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/domain.conf <<EOF
[Service]
Environment=DOMAIN=$DOMAIN
EOF
systemctl daemon-reload
systemctl enable caddy >/dev/null
systemctl restart caddy

echo "==> 5/7 Build y arranque del contenedor (ARM64)"
cd "$APP_DIR"
export APP_VERSION="$(git rev-parse --short HEAD)"
docker compose up -d --build

echo "==> 6/7 Esperando healthcheck"
for i in $(seq 1 60); do
  if curl -fsS --max-time 5 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    echo "    app OK tras ${i}0s aprox."
    break
  fi
  sleep 5
done
curl -fsS --max-time 10 "http://127.0.0.1:8000/health" || {
  echo "ERROR: la app no responde. Revisa: docker compose -f $APP_DIR/docker-compose.yml logs --tail=100 app" >&2
  exit 1
}

echo "==> 7/7 Verificación HTTPS"
for i in $(seq 1 30); do
  if curl -fsS --max-time 10 "https://$DOMAIN/health" >/dev/null 2>&1; then
    echo "    HTTPS OK: https://$DOMAIN"
    break
  fi
  sleep 5
done

echo
echo "Listo. Panel admin: https://$DOMAIN/dev"
echo "Logs: docker compose -f $APP_DIR/docker-compose.yml logs -f app"
echo "Estado: docker compose -f $APP_DIR/docker-compose.yml ps"
