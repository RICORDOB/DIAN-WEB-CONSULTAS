"""Cifrado de credenciales de clientes at-rest (Fernet derivado de APP_SECRET_KEY)."""

import os

import pytest

from app.bot import cifrado


def test_redondo_cifrar_descifrar():
    original = "Gladys123*"
    oculto = cifrado.cifrar(original)
    assert oculto != original
    assert "Gladys" not in oculto
    assert cifrado.descifrar(oculto) == original


def test_clave_estable_misma_app_secret():
    a = cifrado.cifrar("clave")
    b = cifrado.cifrar("clave")
    # Mismo APP_SECRET_KEY -> mismo texto cifrado (Fernet es determinista por llamada,
    # así que solo comprobamos que ambos descifran al mismo valor, no igualdad de cipher).
    assert cifrado.descifrar(a) == cifrado.descifrar(b) == "clave"


def test_descifrado_falla_con_clave_cambiada(monkeypatch):
    # Primero cifra con la clave del entorno actual (fijada en conftest).
    oculto = cifrado.cifrar("secreto123")
    # Luego cambia la clave: descifrar debe fallar (datos ilegibles por diseño).
    monkeypatch.setenv("APP_SECRET_KEY", "otra-clave-distinta-000")
    with pytest.raises(ValueError):
        cifrado.descifrar(oculto)


def test_requiere_clave_suficientemente_larga(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "corta")
    with pytest.raises(RuntimeError):
        cifrado.cifrar("x")


def test_requiere_app_secret_en_produccion(monkeypatch):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(RuntimeError):
        cifrado.cifrar("x")