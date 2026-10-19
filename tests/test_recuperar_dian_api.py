"""Cliente por API de recuperación DIAN: base64, cifrado del portal y errores."""

import base64
import json
from datetime import date

import pytest
import requests

from herramientas.recuperar_dian_api import (
    ClienteRecuperacionDIAN,
    CodigoOTPInvalido,
    ErrorDIAN,
    base64_utf8,
    cifrar_clave,
    clave_cifrado,
    enmascarar,
    normalizar_fecha_expedicion,
)


def test_base64_utf8_igual_al_portal():
    assert base64_utf8("CC-1045498915") == "Q0MtMTA0NTQ5ODkxNQ=="


def test_clave_cifrado_fecha_fija():
    assert clave_cifrado(date(2026, 10, 18)) == "2026101820261018"


def test_cifrar_clave_vector_conocido():
    # Vector generado con el mismo algoritmo del portal (CryptoJS AES-128 ECB PKCS7):
    # imposible cambiarlo sin romper la compatibilidad con el servidor de la DIAN.
    assert cifrar_clave("Prueba1234*", date(2026, 10, 18)) == "K1vmXO+DX3lM2Yp5miWQqw=="


def test_cifrar_clave_determinista_y_bloques_completos():
    a = cifrar_clave("Prueba1234*", date(2026, 10, 18))
    b = cifrar_clave("Prueba1234*", date(2026, 10, 18))
    assert a == b
    assert len(base64.b64decode(a)) % 16 == 0


def test_enmascarar_correo_celular_y_corto():
    assert enmascarar("ricardo@gmail.com") == "ric***@gmail.com"
    assert enmascarar("3001234567") == "300***67"
    assert enmascarar("123") == "***"


def _respuesta(status_code: int, cuerpo: dict) -> requests.Response:
    respuesta = requests.Response()
    respuesta.status_code = status_code
    respuesta._content = json.dumps(cuerpo).encode()
    return respuesta


def test_datos_error_por_codigo():
    with pytest.raises(ErrorDIAN):
        ClienteRecuperacionDIAN._datos(
            _respuesta(400, {"codigo": 400, "mensaje": "Los datos no son correctos"})
        )


def test_datos_exito_sin_codigo():
    datos = ClienteRecuperacionDIAN._datos(
        _respuesta(200, {"status": "ok", "mensaje": "ok", "datos": {}})
    )
    assert datos["status"] == "ok"


def test_datos_json_invalido():
    respuesta = requests.Response()
    respuesta.status_code = 502
    respuesta._content = b"<html>error</html>"
    with pytest.raises(ErrorDIAN):
        ClienteRecuperacionDIAN._datos(respuesta)


def test_normalizar_fecha_expedicion():
    assert normalizar_fecha_expedicion("2010-05-20") == "2010-05-20"
    assert normalizar_fecha_expedicion("20/05/2010") == "2010-05-20"
    assert normalizar_fecha_expedicion("2010/05/20") == "2010-05-20"
    with pytest.raises(ErrorDIAN, match="fecha"):
        normalizar_fecha_expedicion("2010-13-40")


def test_consultar_habilitacion_payload(monkeypatch):
    cliente = ClienteRecuperacionDIAN()
    visto = {}

    def falso_post(ruta, payload, **kwargs):
        visto.update(ruta=ruta, payload=payload)
        return {
            "codigoRta": {"tipoRta": 1, "mensaje": "ok"},
            "datos": {"tieneRut": "true"},
        }

    monkeypatch.setattr(cliente, "_post", falso_post)
    datos = cliente.consultar_habilitacion("CC", "1045498915", "2010-05-20")

    # tipoRta numérico se normaliza a texto; datos queda disponible plano.
    assert datos["tipoRta"] == "1"
    assert datos["mensaje"] == "ok"
    assert datos["datos"] == {"tieneRut": "true"}
    assert visto["ruta"] == "/identidadadministracion/v1/cuentausuarios"
    assert visto["payload"]["operacion"] == {"codigo": "1"}
    assert visto["payload"]["usuariocuenta"]["datosPwd"] == {}
    assert visto["payload"]["usuariocuenta"]["persona"] == {
        "tipoDocumento": "CC",
        "numDocumento": "1045498915",
        "fechaExpedicionDoc": "2010-05-20",
    }


def test_crear_cuenta_envia_clave_en_base64(monkeypatch):
    cliente = ClienteRecuperacionDIAN()
    visto = {}

    def falso_post(ruta, payload, **kwargs):
        visto.update(ruta=ruta, payload=payload)
        return {"codigoRta": {"tipoRta": "1"}}

    monkeypatch.setattr(cliente, "_post", falso_post)
    cliente.crear_cuenta_habilitada(
        "CC", "1045498915", "2010-05-20", "ClaveNueva9*", "123456"
    )

    datos = visto["payload"]["usuariocuenta"]["datosPwd"]
    assert visto["payload"]["operacion"] == {"codigo": "2"}
    assert datos["contrasenaNuevaUsuario"] == base64_utf8("ClaveNueva9*")
    assert datos["contrasenaConfirmacionUsuario"] == base64_utf8("ClaveNueva9*")
    # No debe usar el AES de la recuperación.
    assert datos["contrasenaNuevaUsuario"] != cifrar_clave("ClaveNueva9*")
    assert visto["payload"]["otp"] == "123456"


def test_validar_otp_disponible(monkeypatch):
    cliente = ClienteRecuperacionDIAN()
    visto = {}

    def get_falso(url, params=None, timeout=None):
        visto.update(url=url, params=params)
        return _respuesta(200, {"estado": "DISPONIBLE"})

    monkeypatch.setattr(cliente._http, "get", get_falso)
    datos = cliente.validar_codigo_otp("CC", "1045498915", "ABC 123")

    assert datos["estado"] == "DISPONIBLE"
    assert visto["url"].endswith("/ABC%20123")
    assert visto["params"] == {"userId": "CC-1045498915"}


def test_validar_otp_usado_o_inexistente(monkeypatch):
    cliente = ClienteRecuperacionDIAN()
    monkeypatch.setattr(
        cliente._http, "get", lambda *a, **k: _respuesta(200, {"estado": "USADO"})
    )
    with pytest.raises(CodigoOTPInvalido):
        cliente.validar_codigo_otp("CC", "1045498915", "123456")

    monkeypatch.setattr(
        cliente._http, "get", lambda *a, **k: _respuesta(404, {"mensaje": "no"})
    )
    with pytest.raises(CodigoOTPInvalido):
        cliente.validar_codigo_otp("CC", "1045498915", "123456")
