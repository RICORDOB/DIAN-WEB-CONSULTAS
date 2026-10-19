"""Activación de la cuenta DIAN desde enlace personal: tokens, rutas y bot."""

import pytest
from fastapi.testclient import TestClient

from app import auth, main as mainmod
from app.bot import cifrado, motor, tokens
from app.recuperacion import ErrorRecuperacion
from herramientas.recuperar_dian_api import ErrorDIAN, normalizar_fecha_expedicion


class FakeSesionHabilitacion:
    """Doble de SesionHabilitacionAPI: mismo contrato, sin red."""

    def __init__(self, tipo_documento, numero_documento, dir_trabajo=None):
        self.tipo_documento = tipo_documento
        self.numero_documento = str(numero_documento)
        self.dir_trabajo = dir_trabajo
        self.paso = ""
        self.mensaje = ""
        self.fecha_expedicion = ""
        self.medios_ui = [{"tipo": "email", "etiqueta": "Correo r***@correo.com"}]
        self.cerrada = False

    async def iniciar(self, fecha_expedicion):
        try:
            self.fecha_expedicion = normalizar_fecha_expedicion(fecha_expedicion)
        except ErrorDIAN as exc:
            raise ErrorRecuperacion(str(exc)) from exc
        if self.fecha_expedicion == "1900-01-01":
            raise ErrorRecuperacion("La DIAN no encontró una cuenta con ese documento.")
        self.paso = "menu"
        self.mensaje = "Documento y fecha validados."

    async def enviar_codigo(self, medio=None):
        self.paso = "codigo"
        self.mensaje = "Código enviado al contacto registrado."

    async def reenviar_codigo(self):
        self.mensaje = "Código reenviado."

    async def validar_codigo(self, codigo):
        if codigo == "000000":
            self.paso = "codigo"
            self.mensaje = "La DIAN no aceptó el código."
            raise ErrorRecuperacion(self.mensaje)
        self.paso = "clave"
        self.mensaje = "Código validado."

    async def definir_clave(self, contrasena, confirmacion):
        if contrasena == "Rechazada1*":
            self.paso = "codigo"
            raise ErrorRecuperacion("La DIAN no aceptó el código.")
        self.paso = "exito"
        self.mensaje = "Cuenta habilitada correctamente en la DIAN."

    async def cerrar(self):
        self.cerrada = True


@pytest.fixture(autouse=True)
def _limpiar_estado():
    mainmod._ratelimit.clear()
    mainmod._activaciones.clear()
    motor._chats.clear()
    yield
    mainmod._ratelimit.clear()
    mainmod._activaciones.clear()
    motor._chats.clear()


@pytest.fixture()
def client():
    with TestClient(app=mainmod.app) as c:
        yield c


def _guardar_sin_clave(cedula="1111111111", fecha="2010-05-20"):
    auth.guardar_cliente(
        cedula, "Cédula de Ciudadanía", None, "2027-01-01",
        contrasena_propia=False, telefono="3001234567",
        fecha_expedicion_doc=fecha,
    )


def _token(cedula="1111111111", fecha="2010-05-20"):
    return tokens.emitir_token_activacion(cedula, "Cédula de Ciudadanía", fecha)


# ------------------------------------------------------------------------- //
# Tokens firmados
# ------------------------------------------------------------------------- //
def test_token_activacion_roundtrip():
    t = _token()
    datos = tokens.leer_token_activacion(t)
    assert datos["doc"] == "1111111111"
    assert datos["tipo"] == "Cédula de Ciudadanía"
    assert datos["fecha"] == "2010-05-20"
    assert datos["proposito"] == "activacion"


def test_token_activacion_invalido_falla():
    assert tokens.leer_token_activacion("basura") is None
    assert tokens.leer_token_activacion("") is None


def test_token_activacion_vencido_falla(monkeypatch):
    t = _token()
    monkeypatch.setattr(tokens, "DURACION_ACTIVACION_SEG", -1)
    assert tokens.leer_token_activacion(t) is None


def test_tokens_de_propositos_distintos_no_se_cruzan():
    t_activ = _token()
    assert tokens.consumir_token_descarga(t_activ) is None
    t_desc = tokens.emitir_token_descarga("chat-1", "job-1")
    assert tokens.leer_token_activacion(t_desc) is None


# ------------------------------------------------------------------------- //
# Rutas públicas del enlace
# ------------------------------------------------------------------------- //
def test_info_enlace_valido(client, db):
    _guardar_sin_clave()
    r = client.get(f"/api/activar/{_token()}")
    assert r.status_code == 200
    data = r.json()
    assert data["ya_activada"] is False
    assert data["falta_fecha"] is False
    assert data["numero_documento"].endswith("1111")
    assert "1111111111" not in data["numero_documento"]


def test_info_sin_fecha_en_catalogo_marca_falta_fecha(client, db):
    _guardar_sin_clave(fecha=None)
    r = client.get(f"/api/activar/{_token(fecha='')}")
    assert r.status_code == 200
    assert r.json()["falta_fecha"] is True


def test_info_enlace_invalido_410(client, db):
    r = client.get("/api/activar/token-falso")
    assert r.status_code == 410


def test_info_cliente_inexistente_404(client, db):
    r = client.get(f"/api/activar/{_token(cedula='9999999999')}")
    assert r.status_code == 404


def test_info_ya_activada(client, db):
    _guardar_sin_clave()
    auth.guardar_cliente(
        "1111111111", "Cédula de Ciudadanía", "cifrada-falsa", "2027-01-01",
    )
    r = client.get(f"/api/activar/{_token()}")
    assert r.status_code == 200
    assert r.json()["ya_activada"] is True


def test_pagina_activar(client, db):
    r = client.get("/activar")
    assert r.status_code == 200
    assert "Activa tu cuenta DIAN" in r.text


# ------------------------------------------------------------------------- //
# Flujo guiado (fake de la sesión DIAN)
# ------------------------------------------------------------------------- //
def _iniciar(client, token, **body):
    return client.post(f"/api/activar/{token}/iniciar", json=body)


def _paso(client, token, sesion_id, accion, **extra):
    return client.post(
        f"/api/activar/{token}/paso",
        json={"sesion_id": sesion_id, "accion": accion, **extra},
    )


def test_iniciar_sin_fecha_400(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave(fecha=None)
    r = _iniciar(client, _token(fecha=""))
    assert r.status_code == 400


def test_iniciar_fecha_de_catalogo_rechazada_por_dian(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave(fecha="1900-01-01")
    r = _iniciar(client, _token(fecha="1900-01-01"))
    assert r.status_code == 400
    assert "no encontró" in r.json()["detail"]


def test_iniciar_ya_con_clave_409(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave()
    auth.guardar_cliente(
        "1111111111", "Cédula de Ciudadanía", "cifrada-falsa", "2027-01-01",
    )
    r = _iniciar(client, _token())
    assert r.status_code == 409


def test_flujo_completo_activacion(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave()
    token = _token()

    r = _iniciar(client, token)
    assert r.status_code == 200
    data = r.json()
    assert data["paso"] == "menu" and data["medios"]
    sesion_id = data["sesion_id"]

    r = _paso(client, token, sesion_id, "enviar_codigo", medio="email")
    assert r.json()["paso"] == "codigo"

    r = _paso(client, token, sesion_id, "codigo", codigo="123456")
    assert r.json()["paso"] == "clave"

    r = _paso(client, token, sesion_id, "clave",
              contrasena="Clave123*", confirmacion="Clave123*")
    out = r.json()
    assert out["paso"] == "exito"
    assert out["aviso"] is None

    guardado = auth.buscar_cliente("1111111111")
    assert guardado["contrasena_cifrada"] != "Clave123*"
    assert cifrado.descifrar(guardado["contrasena_cifrada"]) == "Clave123*"
    assert guardado["origen_clave"] == auth.ORIGEN_HABILITADA
    assert guardado["fecha_vencimiento"] == "2027-01-01"
    assert guardado["telefono"] == "3001234567"
    assert guardado["fecha_expedicion_doc"] == "2010-05-20"
    assert auth.cliente_tiene_clave("1111111111")

    # La sesión quedó cerrada y ya no acepta más pasos.
    r = _paso(client, token, sesion_id, "codigo", codigo="123456")
    assert r.status_code == 409


def test_paso_codigo_invalido_regresa_a_codigo(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave()
    token = _token()
    sesion_id = _iniciar(client, token).json()["sesion_id"]
    _paso(client, token, sesion_id, "enviar_codigo")

    r = _paso(client, token, sesion_id, "codigo", codigo="000000")
    assert r.status_code == 400
    data = r.json()
    assert data["paso"] == "codigo"
    assert "código" in data["detail"].lower()


def test_clave_rechazada_por_otp_volver_a_codigo(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave()
    token = _token()
    sesion_id = _iniciar(client, token).json()["sesion_id"]
    _paso(client, token, sesion_id, "enviar_codigo")
    _paso(client, token, sesion_id, "codigo", codigo="123456")

    r = _paso(client, token, sesion_id, "clave",
              contrasena="Rechazada1*", confirmacion="Rechazada1*")
    assert r.status_code == 400
    assert r.json()["paso"] == "codigo"
    assert not auth.cliente_tiene_clave("1111111111")


def test_paso_sesion_ajena_al_token_404(client, db, monkeypatch):
    monkeypatch.setattr(mainmod, "SesionHabilitacionAPI", FakeSesionHabilitacion)
    _guardar_sin_clave()
    _guardar_sin_clave(cedula="2222222222")
    token_a = _token()
    token_b = _token(cedula="2222222222")
    sesion_id = _iniciar(client, token_a).json()["sesion_id"]

    r = _paso(client, token_b, sesion_id, "enviar_codigo")
    assert r.status_code == 404


def test_paso_sesion_inexistente_404(client, db):
    r = _paso(client, _token(), "no-existe", "enviar_codigo")
    assert r.status_code == 404


# ------------------------------------------------------------------------- //
# Endpoint admin: enlace manual
# ------------------------------------------------------------------------- //
def _login_admin(client) -> str:
    r = client.post("/api/login", json={"usuario": "admin", "password": "admin123"})
    assert r.status_code == 200
    return r.cookies.get("sesion")


def test_admin_enlace_requiere_admin(client, db):
    r = client.post("/api/admin/clientes/1111111111/activacion")
    assert r.status_code == 403


def test_admin_enlace_ok(client, db, admin):
    _guardar_sin_clave()
    cookie = _login_admin(client)
    r = client.post("/api/admin/clientes/1111111111/activacion",
                    cookies={"sesion": cookie})
    assert r.status_code == 200
    data = r.json()
    assert data["enlace"].startswith("/activar?t=")
    datos = tokens.leer_token_activacion(data["enlace"].split("t=", 1)[1])
    assert datos["doc"] == "1111111111"
    assert datos["fecha"] == "2010-05-20"


def test_admin_enlace_409_si_tiene_clave(client, db, admin):
    _guardar_sin_clave()
    auth.guardar_cliente(
        "1111111111", "Cédula de Ciudadanía", "cifrada-falsa", "2027-01-01",
    )
    cookie = _login_admin(client)
    r = client.post("/api/admin/clientes/1111111111/activacion",
                    cookies={"sesion": cookie})
    assert r.status_code == 409


def test_admin_enlace_409_sin_fecha(client, db, admin):
    _guardar_sin_clave(fecha=None)
    cookie = _login_admin(client)
    r = client.post("/api/admin/clientes/1111111111/activacion",
                    cookies={"sesion": cookie})
    assert r.status_code == 409


def test_admin_enlace_404_desconocido(client, db, admin):
    cookie = _login_admin(client)
    r = client.post("/api/admin/clientes/9999999999/activacion",
                    cookies={"sesion": cookie})
    assert r.status_code == 404


# ------------------------------------------------------------------------- //
# Bot: cliente registrado sin clave
# ------------------------------------------------------------------------- //
def test_bot_sin_clave_sin_fecha_pide_fecha_y_ofrece_enlace(db):
    _guardar_sin_clave(fecha=None)
    chat = motor.nuevo_chat()

    motor.manejar_mensaje(chat, "1111111111")
    assert motor.estado(chat)["estado"] == motor.ESPERA_FECHA_EXPEDICION

    resp = motor.manejar_mensaje(chat, "20/05/2010")
    assert motor.estado(chat)["estado"] == motor.SIN_CLAVE
    assert "activar" in resp["acciones"]
    url = resp["acciones"]["activar"]["url"]
    assert url.startswith("/activar?t=")
    datos = tokens.leer_token_activacion(url.split("t=", 1)[1])
    assert datos["doc"] == "1111111111"
    assert datos["fecha"] == "2010-05-20"
    assert auth.buscar_cliente("1111111111")["fecha_expedicion_doc"] == "2010-05-20"


def test_bot_fecha_invalida_no_rompe(db):
    _guardar_sin_clave(fecha=None)
    chat = motor.nuevo_chat()
    motor.manejar_mensaje(chat, "1111111111")

    resp = motor.manejar_mensaje(chat, "mañana")
    assert motor.estado(chat)["estado"] == motor.ESPERA_FECHA_EXPEDICION
    assert "fecha" in " ".join(resp["mensajes"]).lower()


def test_bot_sin_clave_con_fecha_ofrece_enlace_directo(db):
    _guardar_sin_clave()
    chat = motor.nuevo_chat()
    resp = motor.manejar_mensaje(chat, "1111111111")
    assert motor.estado(chat)["estado"] == motor.SIN_CLAVE
    assert "activar" in resp["acciones"] and "reintentar" in resp["acciones"]


def test_bot_accion_activar_regenera_enlace(db):
    _guardar_sin_clave()
    chat = motor.nuevo_chat()
    motor.manejar_mensaje(chat, "1111111111")
    resp = motor.manejar_accion(chat, "activar")
    assert resp["acciones"]["activar"]["url"].startswith("/activar?t=")


def test_bot_accion_reintentar_vuelve_a_cedula(db):
    _guardar_sin_clave()
    chat = motor.nuevo_chat()
    motor.manejar_mensaje(chat, "1111111111")
    motor.manejar_accion(chat, "reintentar")
    assert motor.estado(chat)["estado"] == motor.ESPERA_CEDULA


def test_bot_cliente_con_clave_no_entra_a_activacion(db):
    _guardar_sin_clave()
    auth.guardar_cliente(
        "1111111111", "Cédula de Ciudadanía", "cifrada-falsa", "2027-01-01",
    )
    chat = motor.nuevo_chat()
    resp = motor.manejar_mensaje(chat, "1111111111")
    assert motor.estado(chat)["estado"] == motor.ESPERA_OPCION
    assert "rut" in resp["acciones"]
