# Despliegue en Render — pasos y flujo actual

Guía para publicar y actualizar la app web DIAN en Render. La app queda accesible
desde cualquier dispositivo por navegador (https) en `https://dian-web.onrender.com`.

> **Estado real del despliegue:** la app ya está MÁS que la consulta individual:
> incluye el panel Contadores (consultas masivas), notificaciones push, política de
> privacidad y **despliegue continuo** (cada `push a main` despliega automáticamente).

---

## 1. Cómo se actualiza hoy la app (CI automático)

La forma habitual de publicar cambios es **solo hacer `git push` a `main`**:

```bash
git add -A
git commit -m "descripción del cambio"
git push origin main
```

El workflow `.github/workflows/deploy.yml` detecta el push y dispara un deploy en
Render (servicio `srv-d9v6jam7bikc73bt9rn0`, app `dian-web`) usando GitHub Actions.

Para que funcione, el repositorio debe tener configurada la **API key de Render**
como secreto de GitHub:

1. Crea una API key: Render Dashboard → cuenta → **Account → API Keys** (o en
   https://dashboard.render.com/account/api-keys).
2. En el repo de GitHub: **Settings → Secrets and variables → Actions → New
   repository secret**.
3. Nombre: `RENDER_API_KEY`, valor: la key `rnd_...`.

Con eso, cada push a `main` contruye y despliega automáticamente. Puedes revisar el
estado del deploy en GitHub (pestaña **Actions**) o en el dashboard de Render.

> Puedes disparar también un deploy manual desde GitHub en Actions →
> **Deploy a Render → Run workflow**.

---

## 2. Despliegue inicial / manual (crear el Web Service)

Si el proyecto no está aún en Render (o quieres recrearlo), usa el script
`deploy_render.sh`, que crea el Web Service por API (Docker), fija variables de
entorno y espera a que quede `live`:

```bash
export APP_ADMIN_USER=ricardo APP_ADMIN_PASS=tu_clave RENDER_API_KEY=rnd_xxx
./deploy_render.sh
```

O créalo a mano desde el dashboard:

1. https://render.com → **New + → Web Service** → elige el repo `DIAN-WEB-CONSULTAS`.
2. **Environment**: `Docker` (se detecta por el `Dockerfile`).
3. **Branch**: `main`. **Region**: Oregon (default). **Instance Type**: Starter;
   súbela a **Standard** si Chromium falla por memoria.
4. Definir variables (ver sección 3) y **Create Web Service**.

---

## 3. Variables de entorno (Settings → Environment)

| Clave | Necesaria | Descripción |
|---|---|---|
| `APP_ADMIN_USER` | Sí | Usuario del administrador (quien aprueba altas), p. ej. `ricardo` |
| `APP_ADMIN_PASS` | Sí | Contraseña del administrador |
| `APP_SECRET_KEY` | Sí | Firma sesiones. `openssl rand -hex 32`. **Nunca un valor fijo commiteado** |
| `APP_DATA_DIR` | No | Carpeta local de persistencia (default `data`) |
| `APP_JOBS_DIR` | No | Carpeta de trabajos/descargas (default `data/jobs`) |
| `TURSO_DB_URL` | Opcional | URL de la base Turso (libSQL). Con `TURSO_AUTH_TOKEN` activa la nube |
| `TURSO_AUTH_TOKEN` | Opcional | Token de acceso a Turso |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | No | Push. Si faltan, se generan y guardan en `config` |

> `APP_SECRET_KEY` es obligatoria: la app **no arranca** sin ella (fail-fast), salvo
> en desarrollo con `APP_ENV=dev`.

### Nota de seguridad (rotación del secreto)

Si un `APP_SECRET_KEY` fijo llegó a estar en el historial o en un deploy anterior:
1. Genera y fija un **nuevo** `APP_SECRET_KEY` en Render (Settings → Environment).
2. Redeploy (o el push te lo hará) para invalidar las sesiones anteriores.
3. Si el repo es público o compartido, limpia la historia con `git filter-repo`.

---

## 4. Configuración inicial del administrador

Con `APP_ADMIN_USER`/`APP_ADMIN_PASS` definidos, el admin se crea en el primer
arranque. Para operar:

1. Abre la URL de la app e inicia sesión como admin → te lleva al **Panel
   Desarrollador** (`/dev`).
2. Desde ahí: aprueba/rechaza altas, bloquea/desbloquea usuarios y **activa el
   acceso Contador** (consultas masivas de pago).

---

## 5. Flujo de backup local

El respaldo del proyecto se regenera con:

```bash
RENDER_API_KEY=rnd_xxx ./scripts/actualizar_backup.sh
```

Cada corrida crea en `~/Documents/backups-dian/ACTUAL/`:
- `DIAN-WEB-CONSULTAS-ACTUAL.bundle` — historia git completa (verificada al clonar).
- `DIAN-WEB-CONSULTAS-ACTUAL.tar.gz` — working tree + `.git`, sin datos de clientes.
- `CREDENCIALES.txt` (permisos 600) — API de Render y credenciales del admin.
- `RESTAURAR.txt` — guía de restauración con el commit de referencia.

> Sin `RENDER_API_KEY`, el script actualiza bundle/tar/notas y **conserva** el
> `CREDENCIALES.txt` existente. Conviene regenerarlo con la key tras cada cambio.

---

## 6. Problemas comunes

- **Chromium crashea / `Out of memory`**: sube la instancia a **Standard**
  (Settings → Instance Type) y redeploy.
- **No carga la página (502)**: revisa los logs (Sidebar → Logs). Suele ser RAM en
  el arranque → sube la instancia.
- **El admin no aparece**: verifica las tres variables obligatorias en
  Settings → Environment y haz un redeploy.
- **No llega el deploy tras el push**: revisa GitHub Actions; suele faltar el
  secreto `RENDER_API_KEY` (sección 1).
- **Los usuarios ven una versión vieja tras publicar**: la app usa un **cache-buster**
  (`?v=...` en `index/panel/dev/contadores/privacidad`) y un service worker
  (`sw.js` con su propio `CACHE`). Al hacer un cambio visual, **sube el número**
  (p. ej. `r7 → r8` y `dian-web-v6 → v7`) en esas páginas y en `sw.js` para que los
  clientes reciban la nueva versión.

---

## 7. Costo

- **Starter**: US$0/mes, duerme tras 15 min de inactividad (cold start ~30-60 s).
- **Standard**: US$25/mes (o Starter pagado US$7), siempre encendido.
- Sube de plan solo si Chromium necesita más RAM o no quieres demoras de arranque.

---

## 8. Integración con la landing y subdominio (futuro)

ExoRenta se integra con la landing **Contador a tu Servicio** (pestaña "ExoRenta"
en el sitio y enlace en el footer, apuntando a esta URL). La landing usa la env
`NEXT_PUBLIC_EXORENTA_URL` (default `https://dian-web.onrender.com`).

Actualmente no hay dominio propio, por lo que se usa la URL de Render. Cuando se
compre `contadoratuservicio.com`, plan previsto:

1. **Render**: crear el **custom domain** `exorenta.contadoratuservicio.com`
   (Settings → Custom Domains) y seguir las instrucciones de DNS.
2. **DNS**: registrar el CNAME que indique Render (o registro A/ALIAS).
3. **Landing**: actualizar la env `NEXT_PUBLIC_EXORENTA_URL` a
   `https://exorenta.contadoratuservicio.com` y redeployar Vercel.

El resto de la integración no cambia: ExoRenta conserva su propio login
(admin/contador/usuario) y la landing solo lo enlaza/presenta.
