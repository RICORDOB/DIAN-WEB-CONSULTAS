# Despliegue en Render — pasos exactos

Guía paso a paso para publicar la app web DIAN. Una vez hecho, la app queda
accesible desde cualquier dispositivo por navegador (https).

## Requisitos previos (ya hechos)

- Repositorio: https://github.com/RICORDOB/DIAN-WEB-CONSULTAS (privado)
- Contiene `Dockerfile` (Render lo detecta automáticamente).

## 1. Crear cuenta en Render

1. Ve a https://render.com y crea una cuenta (puedes entrar con GitHub).
2. Conecta tu cuenta de GitHub en Render cuando lo pida (Settings → Connect).

## 2. Crear el Web Service

1. En el dashboard de Render, clic en **New +** → **Web Service**.
2. Se abre la lista de repositorios de tu GitHub. Elige **DIAN-WEB-CONSULTAS**.
3. Configuración básica:
   - **Name**: `dian-web` (o el que quieras).
   - **Environment**: `Docker` (Render lo detecta solo por el `Dockerfile`).
   - **Region**: la más cercana (Oregon es buen default).
   - **Branch**: `main`.
   - **Instance Type**: empieza con **Starter** (512 MB). Si Chromium falla
     por falta de memoria, cámbiala a **Standard** (2 GB).

## 3. Variables de entorno (Settings → Environment)

Agrega estas tres (obligatorias):

| Clave | Valor |
|---|---|
| `APP_ADMIN_USER` | usuario del administrador (quien aprueba altas), p. ej. `ricardo` |
| `APP_ADMIN_PASS` | contraseña del administrador (fuerte) |
| `APP_SECRET_KEY` | générala con `openssl rand -hex 32` (aleatoria por despliegue) |

> `APP_SECRET_KEY` firma las sesiones. **Nunca** debe ser un valor fijo commiteado.
> La app **no arranca** si no está definida (fail-fast), salvo en desarrollo con `APP_ENV=dev`.

### Nota de seguridad: rotación del secreto anterior

Una versión anterior de este archivo y de `deploy_render.sh` incluía un valor fijo del
`APP_SECRET_KEY` que quedó en el historial de git. Si ese despliegue se llegó a publicar
con tales valores, considera:

1. Generar y fijar un **nuevo** `APP_SECRET_KEY` en Render (Settings → Environment).
2. En Render: PHP / redeploy (Manual Deploy) para invalidar las sesiones anteriores.
3. Si el repositorio es público o comparte historial con terceros, limpiarlo con
   `git filter-repo` (requiere reescribir la historia y fuerza de push).

### Panel Desarrollador (usuarios + dashboard)

Además de aprobar/rechazar altas, el panel `/dev` permite:

- **Bloquear/desbloquear** el acceso de cualquier usuario. Bloquear **derriba la sesión
  en la siguiente petición** (revocación en vivo), sin esperar la caducidad de la cookie.
- Consultar un **dashboard interactivo**: KPIs de usuarios y consultas, gráficas de
  consultas por día (14 días) y por estado, y el historial de consultas por usuario.
- El número de documento de los clientes **no se persiste** (ni en BD ni en el dashboard).
  El historial guarda solo usuario, tipo de documento, fechas y estado/resultado.

## 4. Desplegar

1. Clic en **Create Web Service**. Render construye la imagen Docker
   (descarga Python + Chromium, tarda unos minutos la primera vez).
2. Cuando el build termine, Render muestra la URL pública de la app, algo como:
   `https://dian-web.onrender.com`.

## 5. Configurar el acceso del administrador

Con `APP_ADMIN_USER`/`APP_ADMIN_PASS` definidos, el admin se crea automáticamente
en el primer arranque. Para acceder:

1. Abre la URL pública de la app.
2. Inicia sesión con el usuario/contraseña de `APP_ADMIN_USER` / `APP_ADMIN_PASS`.
   Render te lleva al **Panel Desarrollador** (`/dev`).
3. Crea la primera cuenta desde la pantalla de login (`Solicitar alta`), y luego
   **Aprueba** esa solicitud desde `/dev`.

## 6. Uso normal

1. Cualquier persona entra a la URL de la app.
2. Se registra → queda pendiente → tú la apruebas desde `/dev`.
3. Con la cuenta aprobada inicia sesión, va a `/panel`, escribe
   **tipo de documento, cédula y contraseña**, y pulsa **CONSULTAR**.
4. Al terminar, el botón **DESCARGAR RESULTADO** entrega el `.xls` de 3 hojas.

## Problemas comunes

- **Chromium crashea / `Out of memory`**: sube la instancia a **Standard** en
  Settings → Instance Type y redepliega (Manual Deploy → Deploy).
- **No carga la página (502)**: revisa los logs (Sidebar → Logs). Suele ser
  falta de RAM durante el arranque → sube la instancia.
- **El admin no aparece**: verifica que las tres variables estén bien escritas
  en Settings → Environment y haz **Manual Deploy**.
- **Necesito saber si un lote masivo va a entrar**: la app actual es SOLO
  consulta individual. La API de masivos se añadirá en una fase futura sobre la
  misma base.

## Actualizar la app después de cambios

1. En tu computadora: `git add -A && git commit -m "cambios" && git push`.
2. En Render: Manual Deploy → Deploy latest commit (o automático si está activo).

## Costo

- **Starter**: US$0/mes para un Web Service que duerme tras 15 min de inactividad.
  Al despertar tarda ~30-60 s en la primera petición.
- **Standard**: US$25/mes (o Starter pagado US$7), siempre encendido y sin "cold start".
- Solo sube de plan si Chromium necesita más RAM o no quieres demoras de arranque.
