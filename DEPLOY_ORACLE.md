# Despliegue en Oracle Cloud (Always Free ARM) — ExoRenta

Guía de la migración desde Render a una VM gratuita de Oracle Cloud
(Ampere A1: **2 OCPU / 12 GB**, Ubuntu 24.04 ARM64).

> **Por qué se migra:** Render Starter (~$7/mes) es el mínimo que soporta Chrome.
> La capa gratuita de Render (512 MB) no aguanta Chromium. Oracle Always Free
> da 12 GB por $0 y la imagen ya es multi-arquitectura.

---

## 0. Antes de tocar nada (respaldo)

1. Exportar las variables de entorno de Render:

   ```bash
   export RENDER_API_KEY=rnd_xxx
   curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
     https://api.render.com/v1/services/srv-d9v6jam7bikc73bt9rn0/env-vars \
     -o /tmp/env-render.json
   ```

2. **Conservar `APP_SECRET_KEY`**: deriva la clave Fernet que cifra las
   contraseñas del catálogo del bot. Si cambia, hay que volver a subir
   `clientes_dian.xlsx`.
3. Conservar `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`: si se regeneran, se
   pierden las suscripciones push existentes.
4. `TURSO_DB_URL` / `TURSO_AUTH_TOKEN`: la base de datos sigue viviendo en Turso,
   no se migra nada.

---

## 1. Crear la VM

1. Cuenta en https://cloud.oracle.com (verificación con tarjeta, sin cobro).
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04** (aarch64)
   - Shape: **VM.Standard.A1.Flex** → 2 OCPU / 12 GB
   - Boot volume: 100 GB
   - **Assign a public IPv4 address**: sí
   - Subir tu llave SSH pública (`~/.ssh/id_ed25519.pub`)
3. Si aparece **"Out of host capacity"**:
   - Probar otra Availability Domain / otra región, o
   - Reintentar con un script cada 5 min, o
   - **Upgrade a Pay As You Go** (los recursos Always Free siguen gratis, pero
     hay prioridad de capacidad y **no aplica el reclaim por inactividad**).
4. En **Networking → VCN → Security List**, permitir ingress TCP **80** y **443**
   (no abras 8000).
5. Reservar la IP pública (**Reserved public IP**) para que no cambie al reiniciar.

---

## 2. DNS gratis con DuckDNS

1. https://www.duckdns.org → login → crear subdominio `exorenta`.
2. Apuntar a la IP reservada de Oracle (el panel de DuckDNS lo hace con un clic).
3. Verificar:

   ```bash
   dig +short exorenta.duckdns.org
   ```

---

## 3. Preparar el servidor

```bash
ssh ubuntu@<IP>
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/RICORDOB/DIAN-WEB-CONSULTAS.git /opt/exorenta-src
```

Crear el `.env` de producción (nunca se commitea):

```bash
cd /opt/exorenta-src
cp .env.example .env
chmod 600 .env
nano .env
```

Valores obligatorios: `APP_SECRET_KEY` (el mismo de Render), `APP_ADMIN_USER`,
`APP_ADMIN_PASS`, `TURSO_DB_URL`, `TURSO_AUTH_TOKEN`, `VAPID_*`.

---

## 4. Desplegar

```bash
cd /opt/exorenta-src
DOMAIN=exorenta.duckdns.org ./scripts/deploy_oracle.sh
```

El script instala Docker + Caddy, configura `ufw` (solo 22/80/443), clona el repo
en `/opt/exorenta`, construye la imagen ARM64, arranca el contenedor y valida
`http://127.0.0.1:8000/health` y `https://<dominio>/health`.

Verificación manual:

```bash
curl -s https://exorenta.duckdns.org/health | python3 -m json.tool
docker compose -f /opt/exorenta/docker-compose.yml ps
docker compose -f /opt/exorenta/docker-compose.yml logs -f app
```

El health debe reportar `"chrome": "ok"` y `"db_backend": "turso"`.

---

## 5. CI/CD

En GitHub → **Settings → Secrets and variables → Actions**:

| Secreto | Valor |
|---|---|
| `SSH_HOST` | IP o `exorenta.duckdns.org` |
| `SSH_USER` | `ubuntu` |
| `SSH_KEY` | llave privada de despliegue (`-----BEGIN OPENSSH PRIVATE KEY-----...`) |

Cada push a `main` hace `git reset --hard`, reconstruye la imagen y valida
`/health`. Si falla, imprime los últimos 150 logs del contenedor.

`deploy-render.yml` queda como plan B manual (`workflow_dispatch`).

---

## 6. Corte y rollback

1. En Vercel, actualizar `NEXT_PUBLIC_EXORENTA_URL` a `https://exorenta.duckdns.org`
   y redesplegar la landing.
2. En Render: **Suspender** el servicio (no borrar). Un servicio suspendido no
   cobra y permite volver atrás en minutos.
3. Tras 2 semanas estable, borrar el servicio de Render.

---

## 7. Operación

```bash
# Actualizar manualmente
cd /opt/exorenta && git pull && docker compose up -d --build

# Backups del volumen (jobs y config)
docker run --rm -v exorenta_data:/data -v /opt/backups:/backup alpine \
  tar czf /backup/exorenta-$(date +%F).tar.gz -C /data .

# Espacio en disco
df -h / && docker system df
```

Monitoreo: UptimeRobot (free, cada 5 min) sobre `https://<dominio>/health`,
alerta a email/WhatsApp.

---

## 8. Riesgos conocidos

| Riesgo | Mitigación |
|---|---|
| Oracle reclama instancias inactivas (p95 CPU/mem/red < 20% en 7 días) | Upgrade a Pay As You Go + budget alert |
| "Out of host capacity" | Reintentos, otra AD/región, o Pay As You Go |
| Chrome ARM bloqueado por MUISCA | Probar consulta real; fallback GCP e2-micro amd64 |
| Cambio de IP | IP pública reservada |
| Un solo worker (jobs en memoria) | Mantener 1 instancia, no escalar |
