"""Cliente por API de recuperación DIAN: base64, cifrado del portal y errores."""

import base64
import json
from datetime import date

import pytest
import requests

from herramientas.recuperar_dian_api import (
    ClienteRecuperacionDIAN,
    ErrorDIAN,
    base64_utf8,
    cifrar_clave,
    clave_cifrado,
    enmascarar,
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
