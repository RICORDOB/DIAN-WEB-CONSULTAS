"""Pruebas de auth: hash, registro/aprobación, bloqueo/desbloqueo y seed de admin."""

import pytest

from app import auth


def test_hash_y_verificacion():
    h, s = auth._hash_password("secreta123")
    assert auth._verificar_password("secreta123", s, h)
    assert not auth._verificar_password("otra123", s, h)


def test_hash_sal_aleatoria():
    h1, s1 = auth._hash_password("x")
    h2, s2 = auth._hash_password("x")
    assert s1 != s2 and h1 != h2


def test_registro_queda_pendiente(db):
    res = auth.registrar("juan", "clave123")
    assert res["estado"] == auth.PENDIENTE
    with pytest.raises(auth.AuthError, match="pendiente"):
        auth.verificar_login("juan", "clave123")


def test_aprobacion_permite_login(db, admin):
    auth.registrar("juan", "clave123")
    with pytest.raises(auth.AuthError):
        auth.verificar_login("juan", "clave123")
    auth.decidir_alta("juan", True, admin)
    data = auth.verificar_login("juan", "clave123")
    assert data["estado"] == auth.APROBADO


def test_rechazo_impide_login(db, admin):
    auth.registrar("juan", "clave123")
    auth.decidir_alta("juan", False, admin)
    with pytest.raises(auth.AuthError, match="rechazada"):
        auth.verificar_login("juan", "clave123")


def test_bloqueo_y_desbloqueo(db, admin):
    auth.registrar("maria", "clave123")
    auth.decidir_alta("maria", True, admin)

    res = auth.bloquear_usuario("maria", True, admin)
    assert res["estado"] == auth.BLOQUEADO
    assert auth.estado_usuario("maria") == auth.BLOQUEADO
    with pytest.raises(auth.AuthError, match="bloqueada"):
        auth.verificar_login("maria", "clave123")

    auth.bloquear_usuario("maria", False, admin)
    assert auth.estado_usuario("maria") == auth.APROBADO
    assert auth.verificar_login("maria", "clave123")["estado"] == auth.APROBADO


def test_usuario_duplicado(db):
    auth.registrar("ana", "clave123")
    with pytest.raises(auth.AuthError, match="ya existe"):
        auth.registrar("ana", "clave456")


def test_admin_seed(db, admin):
    assert auth.es_admin(admin)
    login = auth.verificar_login(admin, "admin123")
    assert login["rol"] == "admin"
    assert login["estado"] == auth.APROBADO


def test_bloquear_usuario_inexistente(db, admin):
    with pytest.raises(auth.AuthError, match="No se encontr"):
        auth.bloquear_usuario("fantasma", True, admin)