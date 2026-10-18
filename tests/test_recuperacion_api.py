"""Tests de SesionRecuperacionAPI (flujo DIAN por API, sin navegador).

El cliente HTTP real se sustituye por ``FakeCliente``: se verifica el armado de
las llamadas, el enmascarado de medios, el enrutado del OTP y que un código
rechazado por la DIAN regrese al paso «codigo».
"""

import asyncio

import pytest

import app.recuperacion_api as api_mod
from app.recuperacion import ErrorRecuperacion
from app.recuperacion_api import SesionRecuperacionAPI, _codigo_tipo
from herramientas.recuperar_dian_api import ErrorDIAN


class FakeCliente:
    """Cliente DIAN en memoria: registra las llamadas y permite forzar errores."""

    instancias: list["FakeCliente"] = []

    def __init__(self):
        self.consultas: list[tuple] = []
        self.codigos: list[tuple] = []
        self.cambios: list[tuple] = []
        self.error_consulta = None
        self.error_cambiar = None
        FakeCliente.instancias.append(self)

    def consultar_documento(self, tipo, numero):
        self.consultas.append((tipo, numero))
        if self.error_consulta:
            raise ErrorDIAN(self.error_consulta)
        return {
            "mediosRecuperacion": [
                {"tipo": "tel1", "valor": "3001234567"},
                {"tipo": "email", "valor": "ricardo@hotmail.com"},
            ],
            "tieneFirmaElectronica": True,
            "tieneRut": True,
        }

    def solicitar_codigo(self, tipo, numero, medio):
        self.codigos.append((tipo, numero, medio))

    def cambiar_clave(self, tipo, numero, clave, codigo, medio, generar_firma=False):
        self.cambios.append((tipo, numero, clave, codigo, medio, generar_firma))
        if self.error_cambiar:
            raise ErrorDIAN(self.error_cambiar)
        return {"mensaje": "Cambio de contraseña exitoso"}


@pytest.fixture()
def fake(monkeypatch):
    FakeCliente.instancias = []
    monkeypatch.setattr(api_mod, "ClienteRecuperacionDIAN", FakeCliente)
    return FakeCliente


def _sesion():
    return SesionRecuperacionAPI("Cédula de Ciudadanía", "1045498915")


def test_flujo_api_completo(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar())

    assert sesion.paso == "menu"
    assert fake.instancias[-1].consultas == [("CC", "1045498915")]
    # El correo se ofrece primero y los valores van enmascarados.
    assert [m["tipo"] for m in sesion.medios_ui] == ["email", "tel1"]
    assert "ric***@hotmail.com" in sesion.medios_ui[0]["etiqueta"]
    assert "3001234567" not in sesion.medios_ui[1]["etiqueta"]

    asyncio.run(sesion.enviar_codigo("tel1"))
    assert sesion.paso == "codigo"
    assert fake.instancias[-1].codigos[-1][2]["tipo"] == "tel1"

    asyncio.run(sesion.aplicar_codigo("AMjU1MDU0"))
    assert sesion.paso == "clave"

    asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))
    assert sesion.paso == "exito"
    llamada = fake.instancias[-1].cambios[-1]
    assert llamada[3] == "AMjU1MDU0"
    assert llamada[5] is True  # con firma electrónica registrada, se regenera

    asyncio.run(sesion.cerrar())
    assert sesion._codigo is None and sesion._cliente is None


def test_codigo_invalido_regresa_al_paso_codigo(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar())
    asyncio.run(sesion.enviar_codigo(None))

    asyncio.run(sesion.aplicar_codigo("000000"))
    fake.instancias[-1].error_cambiar = (
        "Ingreso no válido. Intente nuevamente. (código 404)"
    )
    with pytest.raises(ErrorRecuperacion) as excinfo:
        asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))
    assert sesion.paso == "codigo"
    assert "código" in str(excinfo.value)

    # Con el código correcto el flujo continúa sin reabrir la sesión.
    fake.instancias[-1].error_cambiar = None
    asyncio.run(sesion.aplicar_codigo("123456"))
    asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))
    assert sesion.paso == "exito"


def test_error_de_consulta_se_propaga_legible(fake, monkeypatch):
    def consultar_con_error(self, tipo, numero):
        raise ErrorDIAN("Los datos no son correctos (código 400)")

    monkeypatch.setattr(FakeCliente, "consultar_documento", consultar_con_error)
    sesion = _sesion()
    with pytest.raises(ErrorRecuperacion) as excinfo:
        asyncio.run(sesion.iniciar())
    assert "datos" in str(excinfo.value)


def test_sin_medios_de_recuperacion(fake, monkeypatch):
    monkeypatch.setattr(
        FakeCliente, "consultar_documento", lambda self, tipo, numero: {}
    )
    sesion = _sesion()
    with pytest.raises(ErrorRecuperacion):
        asyncio.run(sesion.iniciar())


def test_enviar_codigo_sin_medio_usa_el_correo(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar())
    asyncio.run(sesion.enviar_codigo(None))
    assert fake.instancias[-1].codigos[-1][2]["tipo"] == "email"


def test_medio_desconocido_es_error_legible(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar())
    with pytest.raises(ErrorRecuperacion):
        asyncio.run(sesion.enviar_codigo("whatsapp"))


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Cédula de Ciudadanía", "CC"),
        ("Cédula de Extranjería", "CE"),
        ("Tarjeta de Extranjería", "TE"),
        ("Tarjeta de Identidad", "TI"),
        ("Registro civil de nacimiento", "RC"),
        ("Certificado registraduría sin identificación", "REGNOID"),
        ("Pasaporte", "PS"),
        ("Permiso especial de permanencia", "PEP"),
        ("Permiso por protección temporal", "PPT"),
        ("cc", "CC"),
        ("", "CC"),
    ],
)
def test_codigo_tipo(texto, esperado):
    assert _codigo_tipo(texto) == esperado
