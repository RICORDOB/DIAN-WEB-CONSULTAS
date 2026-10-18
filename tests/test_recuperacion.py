"""Tests del flujo guiado de recuperación de contraseña DIAN.

El navegador real se sustituye por ``StubRecuperacion`` (mismo contrato que
``SesionRecuperacion``) para probar sin Playwright.
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
    """Sustituto de SesionRecuperacion: sin navegador, estado en memoria."""

    fallar_iniciar = False
    codigo_valido = "123456"
    instancias: list["StubRecuperacion"] = []

    def __init__(self, tipo_documento, numero_documento, dir_trabajo=None):
        self.tipo_documento = tipo_documento
        self.numero_documento = numero_documento
        self.dir_trabajo = dir_trabajo
        self.paso = ""
        self.mensaje = ""
        self.cerrada = False
        StubRecuperacion.instancias.append(self)

    async def iniciar(self):
        if StubRecuperacion.fallar_iniciar:
            # La clase real se cierra a sí misma antes de elevar el error.
            await self.cerrar()
            raise ErrorRecuperacion("No se pudo abrir el portal (stub).")
        self.paso = "menu"
        self.mensaje = "Documento validado (stub)."

    async def enviar_codigo(self):
        self.paso = "codigo"
        self.mensaje = "Código enviado (stub)."

    async def reenviar_codigo(self):
        self.mensaje = "Código reenviado (stub)."

    async def aplicar_codigo(self, codigo):
        if codigo != StubRecuperacion.codigo_valido:
            raise ErrorRecuperacion("El código no es válido (stub).")
        self.paso = "clave"
        self.mensaje = "Código aceptado (stub)."

    async def definir_clave(self, contrasena, confirmacion):
        self.paso = "exito"
        self.mensaje = "Contraseña actualizada (stub)."

    async def cerrar(self):
        self.cerrada = True


@pytest.fixture()
def stub(monkeypatch):
    StubRecuperacion.instancias = []
    StubRecuperacion.fallar_iniciar = False
    monkeypatch.setattr(main_mod, "SesionRecuperacion", StubRecuperacion)
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

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "enviar_codigo"}, cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "codigo"

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


def test_codigo_invalido_no_rompe_la_sesion(client, db, admin, stub):
    cookie = _preparar_usuario(client, admin)
    rec_id = _iniciar(client, cookie).json()["rec_id"]
    _esperar_estado(client, rec_id, cookie, "activo")
    client.post(f"/api/recuperacion/{rec_id}/paso",
                json={"accion": "enviar_codigo"}, cookies={"sesion": cookie})

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "codigo", "codigo": "000000"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200
    datos = r.json()
    assert datos["estado"] == "activo" and "válido" in datos["error"]

    r = client.post(f"/api/recuperacion/{rec_id}/paso",
                    json={"accion": "codigo", "codigo": "123456"},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["paso"] == "clave"


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
    assert "portal" in datos["mensaje"]
    assert stub.instancias[-1].cerrada is True
