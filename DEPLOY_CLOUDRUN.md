# Despliegue en Google Cloud Run — ExoRenta

Migración del servicio desde "Mac encendida + Docker Desktop + túnel Cloudflare"
a **Google Cloud Run** (serverless, escala a cero).

> **Por qué Cloud Run:** el núcleo de ExoRenta es FastAPI + Playwright + Google
> Chrome real (portal MUISCA de la DIAN), que no cabe en Cloudflare Workers. Cloud
> Run ejecuta el mismo contenedor Docker **bajo demanda**: pagas solo el tiempo
> real de consulta y **$0 cuando nadie usa el servicio**.

---

## 0. Por qué no hace falta tocar el código

- **Persistencia en Turso** (nube): usuarios, config, claves VAPID, suscripciones
  push e historial viven en Turso, no en disco local. No se migra nada.
- **Chrome ya va preparado**: `app/navegador.py` lanza con `--no-sandbox`,
  `--disable-dev-shm-usage`, `--disable-gpu` (justo lo que exige Cloud Run).
- **Solo se adapta configuración**: `APP_DATA_DIR`/`APP_JOBS_DIR` se apuntan a
  `/tmp` (el filesystem del contenedor es de solo lectura salvo `/tmp`; los jobs
  son efímeros y se sirven para descarga al terminar la consulta).

---

## 1. Requisitos (una sola vez)

1. **Cuenta de Google Cloud** con facturación activa. La capa gratuita de Cloud
   Run (180 000 vCPU-seg/mes ≈ 50 h de CPU) es **permanente** y cubre de sobra
   el uso esporádico. El *free trial* de $300/90 días es aparte.
2. **Un proyecto** dedicado (recomendado `exorenta`):
   ```bash
   gcloud projects create exorenta --name="ExoRenta"
   gcloud config set project exorenta
   ```
   Si ya existe, solo enlázalo a facturación y actívalo.
3. **gcloud autenticado** en la Mac:
   ```bash
   gcloud auth login
   gcloud auth application-default login   # por si el build lo necesita
   ```

> El proyecto debe tener **facturación enlazada** (Console → Billing → Link a
> billing account). Sin eso, Cloud Run no deja desplegar aunque el coste sea $0.

---

## 2. Variables de entorno

El script lee el `.env` de la raíz del repo y migra automáticamente estas
variables a Cloud Run:

| Variable | Origen |
| --- | --- |
| `APP_SECRET_KEY` | de `.env` (deriva la clave Fernet del catálogo del bot) |
| `APP_ADMIN_USER` / `APP_ADMIN_PASS` | de `.env` |
| `TURSO_DB_URL` / `TURSO_AUTH_TOKEN` | de `.env` |
| `APP_ENV=prod`, `APP_DATA_DIR=/tmp/data`, `APP_JOBS_DIR=/tmp/jobs` | fijas del script |

**Conserva `APP_SECRET_KEY` idéntico**: cambiarlo invalida el cifrado de las
contraseñas del catálogo del bot (habría que volver a subir `clientes_dian.xlsx`).

---

## 3. Desplegar

```bash
./scripts/deploy_cloudrun.sh
```

Qué hace:

1. Activa `run`, `artifactregistry` y `cloudbuild` en el proyecto (idempotente).
2. Crea el repositorio de imágenes `exorenta` en Artifact Registry.
3. Genera `cloudrun.env.yaml` (gitignored) desde `.env`.
4. Construye la imagen **amd64 en Google Cloud Build** (no en la Mac M1).
5. Despliega con: `max-instances=1`, `concurrency=1`, `4Gi/2 vCPU`,
   `timeout=15 min`, `min-instances=0`, acceso público.

Verificación:

```bash
curl -s https://<servicio>.run.app/health
# debe reportar "chrome":"ok" y "db_backend":"turso"
```

---

## 4. Coste: $0 en reposo, pago solo por uso

- Con `min-instances=0`, el contenedor se duerme cuando no hay peticiones: **$0**.
- Cada consulta arranca el contenedor (cold start ~30–60 s por la imagen de
  Chrome) y solo factura esos segundos.
- La capa gratuita (180 000 vCPU-seg / 360 000 GiB-seg / 2 M peticiones al mes)
  absorbe el uso esporádico por completo.

**Temporada de masivo (julio–octubre):** el lote de Contadores procesa en segundo
plano, y Cloud Run lo duerme si no hay peticiones en vuelo. Para esos meses,
deja **una instancia caliente**:

```bash
MIN_INSTANCES=1 ./scripts/deploy_cloudrun.sh
```

Eso cuesta ~$20–40/mes **solo durante la temporada** (una instancia de 4 GiB/2
vCPU siempre encendida), y lo financia el propio servicio de pago. Al terminar,
vuelve a `MIN_INSTANCES=0`.

> Alternativa más limpia si el masivo crece: mover el lote a un *Cloud Run Job*
> (sin límite de tiempo, paga solo el tiempo que corre). Queda para antes de la
> próxima temporada si hace falta.

---

## 5. DNS y corte

1. Apuntar `exorenta.date` al servicio de Cloud Run:
   - Opción A: **dominio personalizado** en Cloud Run (Console → servicio →
     Domain mappings → Add mapping), verificando la propiedad con un TXT en
     Cloudflare y añadiendo los registros A/AAAA que indica Google.
   - Opción B: usar temporalmente la URL `https://<servicio>.run.app`.
2. Actualizar la landing (Vercel) `NEXT_PUBLIC_EXORENTA_URL` a la nueva URL y
   redesplegar.
3. **Apagar lo local** (el alivio en la M1):
   ```bash
   docker compose -f docker-compose.mac.yml down
   # quitar el LaunchAgent supervisor y el túnel cloudflared:
   launchctl unload ~/Library/LaunchAgents/com.exorenta.supervisor.plist 2>/dev/null || true
   # detener cloudflared (según cómo lo tengas configurado)
   ```
   Puedes dejar Docker Desktop instalado; simplemente deja de correr el
   contenedor `exorenta`.

---

## 6. Notas operativas

- **Un solo contenedor, una consulta a la vez** (`concurrency=1`,
  `max-instances=1`): igual que hoy, por la RAM de Chrome y el estado en memoria
  de los jobs/batches.
- **Cold start**: la primera consulta tras inactividad tarda ~30–60 s. Para
  evitarlo en horario pico, sube `MIN_INSTANCES=1`.
- **Estado en memoria**: `_jobs`/`_batches` viven en memoria; si la instancia se
  duerme a media consulta, esa consulta se pierde (no hay datos persistentes en
  riesgo: todo está en Turso). El usuario solo tendría que reintentar.
- **Healthcheck**: configúralo en Console → servicio → Health check apuntando a
  `/health` (el endpoint ya existe).
- **Monitoreo**: UptimeRobot (free, cada 5 min) sobre `https://<dominio>/health`.

---

## 7. Rollback

El antiguo despliegue local (Docker + túnel) sigue intacto hasta que lo apagues
manualmente. Si algo falla en Cloud Run, basta con volver a levantar el
contenedor local y el túnel; el código no cambió y Turso es la misma base.
