"""Tests del flujo guiado de recuperación de contraseña DIAN.

El cliente DIAN se sustituye por ``StubRecuperacion`` (mismo contrato que
``SesionRecuperacionAPI``) para probar sin red. Igual que en el portal real, el
código OTP se valida recién al cambiar la clave: al escribirlo solo se guarda.
"""

import time

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app import auth
from app.bot import cifrado
from app.main import app
from app.recuperacion import ErrorRecuperacion


@pytest.fixture(autouse=True)
def _limpiar_estado():
    main_mod._ratelimit.clear()
    main_mod._recuperaciones.clear()
    yield
    main_mod._recuperaciones.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


class StubRecuperacion:
    """Sustituto de SesionRecuperacionAPI: sin red, estado en memoria."""

    fallar_iniciar = False
    codigo_valido = "123456"
    medios_fijos = [
        {"tipo": "email", "etiqueta": "Correo: ric***@dominio.com"},
        {"tipo": "tel1", "etiqueta": "Celular: ******33"},
    ]
    instancias: list["StubRecuperacion"] = []

    def __init__(self, tipo_documento, numero_documento, dir_trabajo=None):
        self.tipo_documento = tipo_documento
        self.numero_documento = numero_documento
        self.dir_trabajo = dir_trabajo
        self.paso = ""
        self.mensaje = ""
        self.medios_ui = [dict(m) for m in StubRecuperacion.medios_fijos]
        self.medio = None
        self.codigo = None
        self.cerrada = False
        StubRecuperacion.instancias.append(self)

    async def iniciar(self):
        if StubRecuperacion.fallar_iniciar:
            # La clase real también falla antes de quedar utilizable.
            await self.cerrar()
            raise ErrorRecuperacion("No se pudo validar el documento (stub).")
        self.paso = "menu"
        self.mensaje = "Documento validado (stub)."

    async def enviar_codigo(self, medio=None):
        self.medio = medio or (self.medios_ui[0]["tipo"] if self.medios_ui else None)
        self.paso = "codigo"
        self.mensaje = "Código enviado (stub)."

    async def reenviar_codigo(self):
        self.mensaje = "Código reenviado (stub)."

    async def aplicar_codigo(self, codigo):
        # La DIAN valida el código al cambiar la clave (contrato de la versión API).
        self.codigo = codigo
        self.paso = "clave"
        self.mensaje = "Código recibido (stub)."

    async def definir_clave(self, contrasena, confirmacion):
        if self.codigo != StubRecuperacion.codigo_valido:
            self.paso = "codigo"
            self.mensaje = "La DIAN no aceptó el código (stub)."
            raise ErrorRecuperacion(self.mensaje)
        self.paso = "exito"
        self.mensaje = "Contraseña actualizada (stub)."

    async def cerrar(self):
        self.cerrada = True


@pytest.fixture()
def stub(monkeypatch):
    StubRecuperacion.instancias = []
    StubRecuperacion.fallar_iniciar = False
    monkeypatch.setattr(main_mod, "SesionRecuperacionAPI", StubRecuperacion)
    monkeypatch.setattr(main_mod, "MODO_RECUPERACION", "api")
    # En producción el endpoint puede apagarse con RECUPERACION_WEB_ACTIVA=False;
    # estos tests ejercitan el flujo completo, así que se enciende.
    monkeypatch.setattr(main_mod, "RECUPERACION_WEB_ACTIVA", True)
    return StubRecuperacion


def _login(client, usuario, password):
    return client.post("/api/login", json={"usuario": usuario, "password": password})


def _preparar_usuario(client, admin, nombre="pedro", clave="clave123"):
    """Registra, aprueba e inicia sesión de un usuario común."""
    client.post("/api/registro", json={"usuario": nombre, "password": clave})
    cookie_admin = _login(client, admin, "admin123").cookies.get("sesion")
    client.post(
        "/api/admin/decidir",
        json={"usuario": nombre, "aprobar": True},
        cookies={"sesion": cookie_admin},
    )
    return _login(client, nombre, clave).cookies.get("sesion")


def _esperar_estado(client, rec_id, cookie, estado, timeout=3.0):
    """La apertura del navegador corre en segundo plano: sondea hasta el estado."""
    limite = time.time() + timeout
    while time.time() < limite:
        r = client.get(f"/api/recuperacion/{rec_id}", cookies={"sesion": cookie})
        if r.status_code == 200 and r.json()["estado"] == estado:
            return r.json()
        time.sleep(0.02)
    raise AssertionError(f"La recuperación no llegó al estado {estado!r}")


def _iniciar(client, cookie, numero="12345678"):
    return client.post(
        "/api/recuperacion/iniciar",
        json={"tipo_documento": "Cédula de Ciudadanía", "numero_documento": numero},
        cookies={"sesion": cookie},
    )


def test_flujo_completo_actualiza_catalogo(client, db, admin, stub):
    cookie = _preparar_usuario(client, admin)
    auth.guardar_cliente(
        "12345678", "Cédula de Ciudadanía", cifrado.cifrar("clave-vieja"),
        "2027-01-31", contrasena_propia=True,
    )

    r = _iniciar(client, cookie)
    assert r.status_code == 200
    rec_id = r.json()["rec_id"]

    datos = _esperar_estado(client, rec_id, cookie, "activo")
    assert datos["paso"] == "menu" and datos["error"] is None
    # El panel recibe los medios enmascarados para elegir el destino del código.
    assert [m["tipo"] for m in datos["medios"]] == ["email", "tel1"]

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "enviar_codigo", "medio": "tel1"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "codigo"
    assert stub.instancias[-1].medio == "tel1"

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "codigo", "codigo": "123456"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "clave"

    r = client.post(
        f"/api/recuperacion/{rec_id}/paso",
        json={"accion": "clave", "contrasena": "nuevaClave9",
              "confirmacion": "nuevaClave9"},
        cookies={"sesion": cookie},
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "exito"

    guardado = auth.buscar_cliente("12345678")
    assert guardado["contrasena_propia"] == 0
    assert guardado["fecha_vencimiento"] == "2027-01-31"
    assert cifrado.descifrar(guardado["contrasena_cifrada"]) == "nuevaClave9"
    assert stub.instancias[-1].cerrada is True


def test_requiere_sesion(client, db):
    assert client.post("/api/recuperacion/iniciar",
                       json={"numero_documento": "12345678"}).status_code == 401
    assert client.get("/api/recuperacion/abc").status_code == 401


def test_numero_documento_debe_ser_numerico(client, db, admin, stub):
    cookie = _preparar_usuario(client, admin)
    r = _iniciar(client, cookie, numero="ABC123")
    assert r.status_code == 400


def test_aislamiento_entre_usuarios(client, db, admin, stub):
    cookie_pedro = _preparar_usuario(client, admin, nombre="pedro")
    cookie_laura = _preparar_usuario(client, admin, nombre="laura")

    rec_id = _iniciar(client, cookie_pedro).json()["rec_id"]
    _esperar_estado(client, rec_id, cookie_pedro, "activo")

    r = client.get(f"/api/recuperacion/{rec_id}", cookies={"sesion": cookie_laura})
    assert r.status_code == 404
    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "enviar_codigo"}, cookies={"sesion": cookie_laura})
    assert r.status_code == 404


def test_cancelar_cierra_la_sesion(client, db, admin, stub):
    cookie = _preparar_usuario(client, admin)
    rec_id = _iniciar(client, cookie).json()["rec_id"]
    _esperar_estado(client, rec_id, cookie, "activo")

    r = client.post(f"/api/recuperacion/{rec_id}/cancelar", cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["estado"] == "cancelado"
    assert stub.instancias[-1].cerrada is True

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "enviar_codigo"}, cookies={"sesion": cookie})
    assert r.status_code == 409


def test_codigo_invalido_devuelve_al_paso_codigo(client, db, admin, stub):
    """La DIAN valida el OTP al cambiar la clave: un código malo regresa al paso."""
    cookie = _preparar_usuario(client, admin)
    rec_id = _iniciar(client, cookie).json()["rec_id"]
    _esperar_estado(client, rec_id, cookie, "activo")
    client.post(f"/api/recuperacion/{rec_id}/paso",
                json={"accion": "enviar_codigo"}, cookies={"sesion": cookie})

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "codigo", "codigo": "000000"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "clave"

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "clave", "contrasena": "nuevaClave9",
                          "confirmacion": "nuevaClave9"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200
    datos = r.json()
    assert datos["estado"] == "activo" and datos["paso"] == "codigo"
    assert "código" in datos["error"]

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "codigo", "codigo": "123456"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "clave"

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "clave", "contrasena": "nuevaClave9",
                          "confirmacion": "nuevaClave9"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["estado"] == "exito"


def test_enviar_codigo_sin_medio_usa_el_primero(client, db, admin, stub):
    """Compatibilidad: sin selección explícita, la DIAN va al primer medio (correo)."""
    cookie = _preparar_usuario(client, admin)
    rec_id = _iniciar(client, cookie).json()["rec_id"]
    _esperar_estado(client, rec_id, cookie, "activo")

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "enviar_codigo"}, cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "codigo"
    assert stub.instancias[-1].medio == "email"


def test_validaciones_de_clave(client, db, admin, stub):
    cookie = _preparar_usuario(client, admin)
    rec_id = _iniciar(client, cookie).json()["rec_id"]
    _esperar_estado(client, rec_id, cookie, "activo")

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "clave", "contrasena": "corta",
                          "confirmacion": "corta"},
                    cookies={"sesion": cookie})
    assert r.status_code == 400

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "clave", "contrasena": "nuevaClave9",
                          "confirmacion": "otraClave9"},
                    cookies={"sesion": cookie})
    assert r.status_code == 400

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "voltear"}, cookies={"sesion": cookie})
    assert r.status_code == 400


def test_error_al_iniciar_expone_mensaje(client, db, admin, stub):
    stub.fallar_iniciar = True
    cookie = _preparar_usuario(client, admin)
    rec_id = _iniciar(client, cookie).json()["rec_id"]

    datos = _esperar_estado(client, rec_id, cookie, "error")
    assert "documento" in datos["mensaje"]
    assert stub.instancias[-1].cerrada is True


def test_iniciar_deshabilitado_cuando_la_bandera_esta_apagada(
    client, db, admin, stub, monkeypatch
):
    """Con RECUPERACION_WEB_ACTIVA=False (producción) responde 503 legible."""
    monkeypatch.setattr(main_mod, "RECUPERACION_WEB_ACTIVA", False)
    cookie = _preparar_usuario(client, admin)
    r = _iniciar(client, cookie)
    assert r.status_code == 503
    assert "deshabilitado" in r.json()["detail"]
