"""
main — API FastAPI de la app web DIAN.

Rutas:
  GET  /                        -> página de login/registro (HTML)
  GET  /panel                   -> panel de consulta (requiere sesión aprobada)
  GET  /dev                     -> panel del desarrollador (requiere admin)
  GET  /assets/*                -> archivos estáticos
  POST /api/registro            -> solicitar alta (queda pendiente)
  POST /api/login               -> iniciar sesión (usuario aprobado)
  POST /api/logout              -> cerrar sesión
  GET  /api/me                  -> sesión actual
  POST /api/consulta            -> iniciar consulta individual (crea job)
  GET  /api/job/{id}            -> estado/progreso de un job
  GET  /api/job/{id}/descargar  -> descargar el libro .xls resultante
  GET  /api/admin/pendientes    -> solicitudes de alta (requiere admin)
  POST /api/admin/decidir       -> aprobar/rechazar alta (requiere admin)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Cookie, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from .runner import DianRunner

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
JOBS_DIR = Path(os.environ.get("APP_JOBS_DIR", BASE_DIR / "jobs"))

app = FastAPI(title="DIAN Web", docs_url="/docs", openapi_url="/openapi.json")

# Inicializa directorio y base de datos al cargar (además del startup event), de
# modo que funcione también en pruebas que no disparan eventos de ciclo de vida.
JOBS_DIR.mkdir(parents=True, exist_ok=True)
auth.iniciar_db()

# Semáforo: procesa UNA consulta a la vez (economía de RAM en planes pequeños)
_job_lock = asyncio.Lock()

# Almacén en memoria de jobs: id -> {estado, progreso[], final, error, dir}
_jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Seguridad: headers + rate limiting simple (in-memory por IP)
# ---------------------------------------------------------------------------
_ratelimit: dict[str, list[float]] = {}
RATE_VENTANA = 60.0    # en los últimos N segundos

# Rutas sensibles que se protegen con rate limiting
RATE_POR_RUTA = {
    "/api/login": 8,
    "/api/registro": 5,
    "/api/consulta": 10,
}


def _permitido_rate_limit(clave: str, max_req: int) -> bool:
    ahora = time.monotonic()
    ts = _ratelimit.setdefault(clave, [])
    ts[:] = [t for t in ts if ahora - t < RATE_VENTANA]
    if len(ts) >= max_req:
        return False
    ts.append(ahora)
    return True


@app.middleware("http")
async def _middleware_seguridad(request: Request, call_next):
    # Headers de seguridad (el HTTPS lo provee la plataforma)
    if request.url.path in RATE_POR_RUTA:
        ip = request.client.host if request.client else "desconocida"
        if not _permitido_rate_limit(ip, RATE_POR_RUTA[request.url.path]):
            return JSONResponse(
                {"detail": "Demasiados intentos. Espera un momento e intenta de nuevo."},
                status_code=429,
            )
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------
class RegistroIn(BaseModel):
    usuario: str
    password: str


class LoginIn(BaseModel):
    usuario: str
    password: str


class ConsultaIn(BaseModel):
    tipo_documento: str = "Cédula de Ciudadanía"
    numero_documento: str
    contrasena: str


class DecidirIn(BaseModel):
    usuario: str
    aprobar: bool


# ---------------------------------------------------------------------------
# Utilidades de sesión
# ---------------------------------------------------------------------------
def _sesion(token: Optional[str]) -> Optional[tuple[str, str]]:
    """Valida el token Y el estado actual del usuario en BD (revocación en vivo:
    si el admin bloquea a alguien, su sesión deja de valer en la siguiente petición)."""
    data = auth.leer_token(token or "")
    if not data:
        return None
    usuario, rol = data["usuario"], data["rol"]
    if rol != "admin":
        if auth.estado_usuario(usuario) != auth.APROBADO:
            return None
    return (usuario, rol)


# ---------------------------------------------------------------------------
# Vistas HTML
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def pag_inicio():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/panel", response_class=HTMLResponse)
async def pag_panel(sesion: str | None = Cookie(default=None)):
    if not _sesion(sesion):
        return RedirectResponse("/", status_code=303)
    html = (STATIC_DIR / "panel.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/dev", response_class=HTMLResponse)
async def pag_dev(sesion: str | None = Cookie(default=None)):
    s = _sesion(sesion)
    if not s or s[1] != "admin":
        return RedirectResponse("/panel", status_code=303)
    html = (STATIC_DIR / "dev.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


# ---------------------------------------------------------------------------
# API: autenticación
# ---------------------------------------------------------------------------
@app.post("/api/registro")
async def api_registro(body: RegistroIn):
    try:
        res = auth.registrar(body.usuario, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "mensaje": "Solicitud de alta creada. Espera la aprobación del administrador.",
        "usuario": res["usuario"],
        "estado": res["estado"],
    }


@app.post("/api/login")
async def api_login(body: LoginIn):
    try:
        data = auth.verificar_login(body.usuario, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    token = auth.crear_token(data["usuario"], data["rol"])
    resp = JSONResponse({"ok": True, "rol": data["rol"]})
    resp.set_cookie("sesion", token, httponly=True, samesite="lax", max_age=auth.SESSION_MAX_AGE)
    return resp


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sesion")
    return resp


@app.get("/api/me")
async def api_me(sesion: str | None = Cookie(default=None)):
    s = _sesion(sesion)
    if not s:
        return {"autenticado": False}
    return {"autenticado": True, "usuario": s[0], "rol": s[1]}


# ---------------------------------------------------------------------------
# API: consulta (job)
# ---------------------------------------------------------------------------
@app.post("/api/consulta")
async def api_consulta(body: ConsultaIn, sesion: str | None = Cookie(default=None)):
    s = _sesion(sesion)
    if not s:
        raise HTTPException(status_code=401, detail="No autenticado.")
    usuario = s[0]

    numero = body.numero_documento.strip()
    if not numero or not body.contrasena:
        raise HTTPException(status_code=400, detail="Cédula y contraseña son obligatorias.")
    if not numero.isdigit():
        raise HTTPException(status_code=400, detail="El número de cédula debe ser numérico.")

    job_id = uuid.uuid4().hex
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    auth.registrar_consulta(job_id, usuario, body.tipo_documento)
    _jobs[job_id] = {
        "estado": "queued",
        "progreso": [],
        "final": None,
        "error": None,
        "dir": job_dir,
        "usuario": usuario,
        "creado": time.time(),
    }

    async def _ejecutar():
        async with _job_lock:
            job = _jobs[job_id]
            job["estado"] = "running"

            def cb(msg: str, _done: bool):
                # Los hitos "done" del runner son informativos (progreso), no
                # señalan el fin real del job; ese estado lo fija el orquestador.
                job["progreso"].append(msg)

            runner = DianRunner(job_dir=job_dir, progreso=cb)
            try:
                final = await runner.consulta_individual(
                    body.tipo_documento, numero, body.contrasena
                )
                job["estado"] = "done"
                job["final"] = str(final)
                auth.actualizar_consulta(
                    job_id, auth.ESTADO_DONE, resultado=Path(final).name
                )
            except Exception as exc:  # noqa: BLE001
                job["estado"] = "error"
                job["error"] = f"{type(exc).__name__}: {exc}"
                runner.emitir_done(f"Error: {exc}")
                job["progreso"].append(f"Error: {exc}")
                auth.actualizar_consulta(
                    job_id, auth.ESTADO_ERROR, error=str(exc)
                )

    asyncio.create_task(_ejecutar())
    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
async def api_job(job_id: str, sesion: str | None = Cookie(default=None)):
    s = _sesion(sesion)
    job = _jobs.get(job_id)
    if not s or not job or job["usuario"] != s[0]:
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    return {
        "estado": job["estado"],
        "progreso": job["progreso"][-50:],
        "final": job["final"],
        "error": job["error"],
    }


@app.get("/api/job/{job_id}/descargar")
async def api_descargar(job_id: str, sesion: str | None = Cookie(default=None)):
    s = _sesion(sesion)
    job = _jobs.get(job_id)
    if not s or not job or job["usuario"] != s[0]:
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    if not job["final"] or not Path(job["final"]).exists():
        raise HTTPException(status_code=409, detail="El resultado aún no está listo.")
    return FileResponse(
        job["final"],
        filename=Path(job["final"]).name,
        media_type="application/vnd.ms-excel",
    )


def _limpiar_jobs_viejos() -> None:
    """Limpia jobs terminados con más de 1 hora de antigüedad (libera espacio
    sin borrar resultados recién generados)."""
    ahora = time.time()
    for job_id in list(_jobs.keys()):
        job = _jobs[job_id]
        if job["estado"] in ("done", "error") and ahora - job["creado"] > 3600:
            shutil.rmtree(job["dir"], ignore_errors=True)
            _jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# API: panel desarrollador
# ---------------------------------------------------------------------------
def _requiere_admin(sesion) -> tuple[str, str]:
    s = _sesion(sesion)
    if not s or s[1] != "admin":
        raise HTTPException(status_code=403, detail="Requiere rol de administrador.")
    return s


@app.get("/api/admin/pendientes")
async def api_pendientes(sesion: str | None = Cookie(default=None)):
    _requiere_admin(sesion)
    return {"pendientes": auth.listar_pendientes()}


@app.post("/api/admin/decidir")
async def api_decidir(body: DecidirIn, sesion: str | None = Cookie(default=None)):
    admin = _requiere_admin(sesion)[0]
    if body.usuario == admin:
        raise HTTPException(status_code=400, detail="No puedes modificar tu propia cuenta.")
    try:
        res = auth.decidir_alta(body.usuario, body.aprobar, admin)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return res


class BloquearIn(BaseModel):
    usuario: str
    bloquear: bool


@app.post("/api/admin/bloquear")
async def api_bloquear(body: BloquearIn, sesion: str | None = Cookie(default=None)):
    """Bloquea o desbloquea el acceso de un usuario (derriba su sesión en vivo)."""
    admin = _requiere_admin(sesion)[0]
    if body.usuario == admin:
        raise HTTPException(status_code=400, detail="No puedes bloquearte a ti mismo.")
    try:
        res = auth.bloquear_usuario(body.usuario, body.bloquear, admin)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return res


@app.get("/api/admin/estadisticas")
async def api_estadisticas(sesion: str | None = Cookie(default=None)):
    _requiere_admin(sesion)
    return auth.estadisticas_completas()


@app.get("/api/admin/consultas")
async def api_consultas(sesion: str | None = Cookie(default=None),
                        usuario: str | None = None,
                        estado: str | None = None,
                        limite: int = 100):
    """Historial de consultas para el dashboard (sin número de documento)."""
    _requiere_admin(sesion)
    return {"consultas": auth.listar_consultas(usuario, estado, min(limite, 200))}


# ---------------------------------------------------------------------------
# Inicialización
# ---------------------------------------------------------------------------
async def _limpieza_periodica() -> None:
    """Barre los jobs viejos cada 30 minutos mientras la app esté viva."""
    while True:
        await asyncio.sleep(30 * 60)
        _limpiar_jobs_viejos()


@app.on_event("startup")
async def _startup():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    auth.iniciar_db()
    auth.marcar_consultas_huerfanas()
    _limpiar_jobs_viejos()
    asyncio.create_task(_limpieza_periodica())