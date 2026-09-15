"""Motor del chat bot: máquina de estados, catálogo de clientes y token de descarga."""

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import auth
from app.bot import cifrado, motor, tokens
from app.main import _ratelimit, app


@pytest.fixture(autouse=True)
def _limpiar_chats_y_tokens():
    _ratelimit.clear()
    motor._chats.clear()
    tokens._tokens_descarga.clear()
    yield
    _ratelimit.clear()
    motor._chats.clear()
    tokens._tokens_descarga.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _guardar_cliente(cedula="31200506", contrasena="Gladys123*"):
    auth.guardar_cliente(cedula, "Cédula de Ciudadanía",
                         cifrado.cifrar(contrasena), "2026-08-14")


# ------------------------------------------------------------------------- //
# Motor (máquina de estados)
# ------------------------------------------------------------------------- //
def test_motor_desconocido_no_cliente():
    chat = motor.nuevo_chat()
    resp = motor.manejar_mensaje(chat, "1111111111")
    assert motor.estado(chat)["estado"] == motor.NO_CLIENTE
    assert "no encuentro" in " ".join(resp["mensajes"]).lower()
    assert "manual" in resp["acciones"]


def test_motor_saludo_devuelve_bienvenida():
    chat = motor.nuevo_chat()
    resp = motor.manejar_mensaje(chat, "hola")
    assert "asistente" in " ".join(resp["mensajes"]).lower()


def test_motor_cedula_invalida_reintenta():
    chat = motor.nuevo_chat()
    resp = motor.manejar_mensaje(chat, "abc")
    assert "dígitos" in " ".join(resp["mensajes"]).lower()


def test_motor_flujo_cliente_hasta_lanzar():
    _guardar_cliente()
    chat = motor.nuevo_chat()
    resp = motor.manejar_mensaje(chat, "31200506")
    assert motor.estado(chat)["estado"] == motor.ESPERA_OPCION
    assert "rut" in resp["acciones"] and "consulta" in resp["acciones"]

    resp = motor.manejar_accion(chat, "rut")
    assert motor.estado(chat)["estado"] == motor.PROCESANDO
    lanzar = resp["lanzar"]
    assert lanzar["tipo"] == "rut"
    assert lanzar["numero_documento"] == "31200506"
    assert lanzar["contrasena"] == "Gladys123*"  # descifrada en el momento


def test_motor_no_cliente_reintentar():
    chat = motor.nuevo_chat()
    motor.manejar_mensaje(chat, "1111111111")
    resp = motor.manejar_accion(chat, "reintentar")
    assert motor.estado(chat)["estado"] == motor.ESPERA_CEDULA
    assert "cédula" in " ".join(resp["mensajes"]).lower()


def test_motor_conversacion_expirada(monkeypatch):
    chat = motor.nuevo_chat()
    monkeypatch.setattr(motor, "INACTIVIDAD_SEG", 0)
    assert motor.estado(chat) is None  # vence al primer acceso


# ------------------------------------------------------------------------- //
# Tokens de descarga (1-uso + expiración)
# ------------------------------------------------------------------------- //
def test_token_descarga_se_consume_una_vez():
    t = tokens.emitir_token_descarga("chat-1", "job-1")
    assert tokens.consumir_token_descarga(t) == ("chat-1", "job-1")
    assert tokens.consumir_token_descarga(t) is None  # ya usado


def test_token_descarga_invalido_falla():
    assert tokens.consumir_token_descarga("basura") is None


def test_token_descarga_vencido_falla(monkeypatch):
    t = tokens.emitir_token_descarga("chat-1", "job-1")
    monkeypatch.setattr(tokens, "DURACION_DESCARGA_SEG", -1)
    assert tokens.consumir_token_descarga(t) is None


# ------------------------------------------------------------------------- //
# API: subida admin y chat público
# ------------------------------------------------------------------------- //
def _login_admin(client) -> str:
    r = client.post("/api/login", json={"usuario": "admin", "password": "admin123"})
    assert r.status_code == 200
    return r.cookies.get("sesion")


def _xlsx_clientes_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["tipo_documento", "numero_documento", "contrasena",
               "fecha_vencimiento", "estado"])
    ws.append(["Cédula de Ciudadanía", "31200506", "Gladys123*", "2026-08-14", "ok"])
    ws.append(["Cédula de Ciudadanía", "71979803", "Elkin123*", "2026-08-13", "ok"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_admin_clientes_requiere_admin(client, db):
    r = client.post("/api/admin/clientes",
                    files={"archivo": ("c.xlsx", _xlsx_clientes_bytes(),
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 403


def test_admin_subir_clientes(client, db, admin):
    cookie = _login_admin(client)
    r = client.post("/api/admin/clientes",
                    files={"archivo": ("c.xlsx", _xlsx_clientes_bytes(),
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    cookies={"sesion": cookie})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2 and data["cargados"] == 2

    r = client.get("/api/admin/clientes", cookies={"sesion": cookie})
    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert all("contrasena_cifrada" not in c or "contrasena" not in c
               for c in r.json()["clientes"])

    # La credencial quedó cifrada y legible solo con la clave del entorno.
    guardado = auth.buscar_cliente("31200506")
    assert guardado["contrasena_cifrada"] != "Gladys123*"
    assert cifrado.descifrar(guardado["contrasena_cifrada"]) == "Gladys123*"


def test_admin_eliminar_cliente(client, db, admin):
    cookie = _login_admin(client)
    _guardar_cliente()
    r = client.delete("/api/admin/clientes/31200506", cookies={"sesion": cookie})
    assert r.status_code == 200
    assert auth.buscar_cliente("31200506") is None


def test_bot_mensaje_publico_identifica_cliente(client, db):
    _guardar_cliente()
    r = client.post("/api/bot/mensaje", json={"mensaje": "hola"})
    assert r.status_code == 200
    assert r.json()["cookie"]  # cookie nueva de chat
    bot = r.json()["cookie"]

    r = client.post("/api/bot/mensaje", json={"mensaje": "31200506"},
                    cookies={"bot": bot})
    assert r.status_code == 200
    data = r.json()
    assert "rut" in data["acciones"] and "consulta" in data["acciones"]


def test_bot_mensaje_no_cliente(client, db):
    r = client.post("/api/bot/mensaje", json={"mensaje": "99999999999"})
    data = r.json()
    assert "manual" in data["acciones"]


def test_bot_accion_lanza_job_y_descarga(client, db, monkeypatch, tmp_path):
    from app import main as mainmod

    class RunnerStub:
        def __init__(self, job_dir, progreso=None):
            self.ultimo_analisis = None
            self.ultima_fecha_vencimiento = None

        async def descargar_certificado_rut(self, tipo, numero, contrasena):
            ruta = tmp_path / "RUT_31200506.pdf"
            ruta.write_bytes(b"%PDF-1.4 bot")
            return ruta

    monkeypatch.setattr(mainmod, "DianRunner", RunnerStub)
    _guardar_cliente()
    chat, cookie = _chat_nuevo(client)

    client.post("/api/bot/mensaje", json={"mensaje": "31200506"}, cookies={"bot": cookie})
    r = client.post("/api/bot/accion", json={"accion": "rut"}, cookies={"bot": cookie})
    assert r.status_code == 200
    data = r.json()
    assert "job_id" in data and data["job_id"]
    job_id = data["job_id"]

    # espera el job
    estado = {}
    for _ in range(60):
        estado = client.get("/api/bot/job/" + job_id, cookies={"bot": cookie}).json()
        if estado["estado"] in ("done", "error"):
            break
        import time
        time.sleep(0.05)
    assert estado["estado"] == "done", estado.get("error")
    assert "descarga" in estado

    r = client.get(estado["descarga"], cookies={"bot": cookie})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")

    # Solo una vez: el mismo enlace ya no sirve
    r2 = client.get(estado["descarga"], cookies={"bot": cookie})
    assert r2.status_code == 403


def _chat_nuevo(client):
    r = client.post("/api/bot/mensaje", json={"mensaje": "hola"})
    return None, r.json()["cookie"]