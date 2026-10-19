"""Tests de SesionHabilitacionAPI y de la política de claves de la DIAN.

El cliente HTTP se sustituye por un doble en memoria: se verifica el orden del
flujo (validar → OTP → clave), el enmascarado de medios, los rechazos de la
DIAN y que una clave débil no llegue a habilitar la cuenta.
"""

import asyncio

import pytest

import app.habilitacion_api as hab_mod
from app.habilitacion_api import SesionHabilitacionAPI
from app.recuperacion import ErrorRecuperacion
from herramientas.comun import errores_politica_clave, generar_clave_dian
from herramientas.recuperar_dian_api import CodigoOTPInvalido, ErrorDIAN


class FakeCliente:
    """Cliente DIAN en memoria: registra llamadas y permite forzar respuestas."""

    def __init__(self):
        self.tipo_rta = "1"
        self.rta_final = "1"
        self.medios = [
            {"tipo": "sms", "valor": "3001234567"},
            {"tipo": "email", "valor": "ricardo@hotmail.com"},
        ]
        self.tiene_rut = "true"
        self.tiene_firma = "false"
        self.consultas: list[tuple] = []
        self.codigos: list[tuple] = []
        self.creados: list[tuple] = []
        self.error_validar = None
        self.error_crear = None

    def consultar_habilitacion(self, tipo, numero, fecha):
        self.consultas.append((tipo, numero, fecha))
        return {
            "tipoRta": self.tipo_rta,
            "datos": {
                "mediosRecuperacion": self.medios,
                "tieneRut": self.tiene_rut,
                "tieneFirmaElectronica": self.tiene_firma,
            },
        }

    def solicitar_codigo(self, tipo, numero, medio):
        self.codigos.append((tipo, numero, medio))

    def validar_codigo_otp(self, tipo, numero, codigo):
        if self.error_validar:
            raise self.error_validar
        return {"estado": "DISPONIBLE"}

    def crear_cuenta_habilitada(self, tipo, numero, fecha, clave, codigo):
        if self.error_crear:
            raise self.error_crear
        self.creados.append((tipo, numero, fecha, clave, codigo))
        return {"codigoRta": {"tipoRta": self.rta_final}}


@pytest.fixture()
def fake(monkeypatch):
    cliente = FakeCliente()
    monkeypatch.setattr(hab_mod, "ClienteRecuperacionDIAN", lambda: cliente)
    return cliente


def _sesion():
    return SesionHabilitacionAPI("Cédula de Ciudadanía", "1045498915")


def test_flujo_completo(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))

    assert sesion.paso == "menu"
    assert sesion.fecha_expedicion == "2010-05-20"
    assert fake.consultas == [("CC", "1045498915", "2010-05-20")]
    # El correo se ofrece primero y los valores nunca salen completos.
    assert [m["tipo"] for m in sesion.medios_ui] == ["email", "sms"]
    assert "ric***@hotmail.com" in sesion.medios_ui[0]["etiqueta"]
    assert "3001234567" not in sesion.medios_ui[1]["etiqueta"]
    assert sesion.tiene_rut is True and sesion.tiene_firma is False

    asyncio.run(sesion.enviar_codigo("sms"))
    assert sesion.paso == "codigo"
    assert fake.codigos[-1][2]["tipo"] == "sms"

    asyncio.run(sesion.validar_codigo("123456"))
    assert sesion.paso == "clave"

    asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))
    assert sesion.paso == "exito"
    assert fake.creados[-1] == (
        "CC",
        "1045498915",
        "2010-05-20",
        "ClaveNueva9*",
        "123456",
    )

    asyncio.run(sesion.cerrar())
    assert sesion._cliente is None and sesion._codigo is None


def test_documento_ya_tiene_cuenta(fake):
    fake.tipo_rta = "3"
    with pytest.raises(ErrorRecuperacion, match="ya tiene una cuenta"):
        asyncio.run(_sesion().iniciar("2010-05-20"))


def test_documento_no_encontrado(fake):
    fake.tipo_rta = "2"
    with pytest.raises(ErrorRecuperacion, match="no encontró"):
        asyncio.run(_sesion().iniciar("2010-05-20"))


def test_sin_medios_de_contacto(fake):
    fake.medios = []
    with pytest.raises(ErrorRecuperacion, match="no reporta"):
        asyncio.run(_sesion().iniciar("2010-05-20"))


def test_fecha_invalida_no_llama_a_la_dian(fake):
    with pytest.raises(ErrorRecuperacion, match="fecha"):
        asyncio.run(_sesion().iniciar("2010-13-40"))
    assert fake.consultas == []


def test_medio_no_disponible(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))
    with pytest.raises(ErrorRecuperacion, match="no reporta un medio"):
        asyncio.run(sesion.enviar_codigo("whatsapp"))


def test_otp_invalido_regresa_al_paso_codigo(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))
    asyncio.run(sesion.enviar_codigo(None))

    fake.error_validar = CodigoOTPInvalido("El código ya venció.")
    with pytest.raises(ErrorRecuperacion, match="no aceptó"):
        asyncio.run(sesion.validar_codigo("000000"))
    assert sesion.paso == "codigo"

    # Un código nuevo continúa el flujo sin volver a empezar.
    fake.error_validar = None
    asyncio.run(sesion.validar_codigo("123456"))
    asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))
    assert sesion.paso == "exito"


def test_clave_debil_no_habilita(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))
    asyncio.run(sesion.enviar_codigo(None))
    asyncio.run(sesion.validar_codigo("123456"))

    with pytest.raises(ErrorRecuperacion, match="requisitos"):
        asyncio.run(sesion.definir_clave("corta", "corta"))
    assert sesion.paso == "clave"
    assert fake.creados == []


def test_claves_distintas_no_habilita(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))
    asyncio.run(sesion.enviar_codigo(None))
    asyncio.run(sesion.validar_codigo("123456"))

    with pytest.raises(ErrorRecuperacion, match="no coinciden"):
        asyncio.run(sesion.definir_clave("ClaveNueva9*", "OtraClave9*"))
    assert fake.creados == []


def test_otp_rechazado_en_el_paso_final_regresa_al_codigo(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))
    asyncio.run(sesion.enviar_codigo(None))
    asyncio.run(sesion.validar_codigo("123456"))

    fake.error_crear = ErrorDIAN("Ingreso no válido. Intente nuevamente. (código 404)")
    with pytest.raises(ErrorRecuperacion, match="no aceptó"):
        asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))
    assert sesion.paso == "codigo"


def test_cuenta_habilitada_por_otra_sesion(fake):
    sesion = _sesion()
    asyncio.run(sesion.iniciar("2010-05-20"))
    asyncio.run(sesion.enviar_codigo(None))
    asyncio.run(sesion.validar_codigo("123456"))

    fake.rta_final = "3"
    with pytest.raises(ErrorRecuperacion, match="ya estaba habilitada"):
        asyncio.run(sesion.definir_clave("ClaveNueva9*", "ClaveNueva9*"))


@pytest.mark.parametrize(
    "clave",
    [
        "Ab1*",            # corta
        "abcdefgh1*",      # sin mayúscula
        "ABCDEFGH1*",      # sin minúscula
        "Abcdefgh*",       # sin número
        "Abcdefgh1",       # sin especial
        "aaaaaa1*A",       # 6 repetidos
        "Ábcdefg1*",       # con acento
        "Abc def1*",       # con espacio
    ],
)
def test_politica_rechaza_claves_invalidas(clave):
    assert errores_politica_clave(clave)


def test_politica_acepta_clave_generada():
    for _ in range(20):
        clave = generar_clave_dian()
        assert 6 <= len(clave) <= 15
        assert errores_politica_clave(clave) == []
