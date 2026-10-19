"""Pruebas de auth: hash, registro/aprobación, bloqueo/desbloqueo y seed de admin."""

import os
import sqlite3

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


def test_telefono_cliente_se_guarda_y_conserva(db):
    auth.guardar_cliente("111", "CC", "cif1", "2026-10-22",
                         telefono="3001234567")
    c = auth.listar_clientes()[0]
    assert c["telefono"] == "3001234567"

    # Re-subida sin teléfono: se conserva el anterior (COALESCE).
    auth.guardar_cliente("111", "CC", "cif2", "2026-10-22")
    assert auth.listar_clientes()[0]["telefono"] == "3001234567"

    # Con teléfono nuevo: se actualiza (y se limpia el espaciado).
    auth.guardar_cliente("111", "CC", "cif3", "2026-10-23",
                         telefono=" 310 555 0000 ")
    c = auth.listar_clientes()[0]
    assert c["telefono"] == "310 555 0000"
    assert c["fecha_vencimiento"] == "2026-10-23"
    assert "contrasena" not in c and "contrasena_cifrada" not in c


def test_listar_usuarios_suscritos(db):
    assert auth.listar_usuarios_suscritos() == []
    auth.guardar_suscripcion("ana", "https://fcm/a", "k1", "a1")
    auth.guardar_suscripcion("ana", "https://fcm/b", "k2", "a2")
    auth.guardar_suscripcion("luis", "https://fcm/c", "k3", "a3")
    assert sorted(auth.listar_usuarios_suscritos()) == ["ana", "luis"]


def test_admin_reset_cuando_cambia_env(db, admin):
    # El admin ya existe con clave "admin123" (fixture).
    assert auth.verificar_login(admin, "admin123")["rol"] == "admin"

    # Se cambia la variable de entorno a una clave nueva y se re-siembra.
    os.environ["APP_ADMIN_PASS"] = "nueva-clave-2026"
    auth.iniciar_db()

    # La clave vieja ya no entra; la nueva sí.
    with pytest.raises(auth.AuthError, match="incorrectos"):
        auth.verificar_login(admin, "admin123")
    login = auth.verificar_login(admin, "nueva-clave-2026")
    assert login["rol"] == "admin"
    assert login["estado"] == auth.APROBADO


def test_admin_siembra_no_toca_si_coincide(db, admin):
    with auth._conectar() as conn:
        fila = conn.execute(
            "SELECT hash, salt FROM usuarios WHERE usuario = ?", (admin,)
        ).fetchone()

    os.environ["APP_ADMIN_PASS"] = "admin123"
    auth.iniciar_db()

    with auth._conectar() as conn:
        fila2 = conn.execute(
            "SELECT hash, salt FROM usuarios WHERE usuario = ?", (admin,)
        ).fetchone()
    assert fila["hash"] == fila2["hash"]
    assert fila["salt"] == fila2["salt"]


def _columnas_clientes() -> dict:
    with auth._conectar() as conn:
        return {
            r["name"]: r
            for r in conn.execute(
                "PRAGMA table_info(clientes_autorizados)"
            ).fetchall()
        }


def test_esquema_nuevo_permite_cliente_sin_clave(db):
    assert _columnas_clientes()["contrasena_cifrada"]["notnull"] == 0

    auth.guardar_cliente("222", "CC", None, None,
                         fecha_expedicion_doc="2010-05-20")
    assert auth.cliente_tiene_clave("222") is False
    assert auth.contar_clientes_sin_clave() == 1
    cliente = auth.listar_clientes()[0]
    assert cliente["tiene_clave"] == 0
    assert cliente["origen_clave"] is None
    assert cliente["fecha_expedicion_doc"] == "2010-05-20"


def test_guardar_cliente_conserva_la_clave_si_llega_vacia(db):
    auth.guardar_cliente("333", "CC", "cif1", None,
                         origen_clave=auth.ORIGEN_HABILITADA)
    assert auth.cliente_tiene_clave("333") is True

    # Re-subida del catálogo sin clave: se conserva la habilitada.
    auth.guardar_cliente("333", "CC", None, None)
    cliente = auth.listar_clientes()[0]
    assert cliente["tiene_clave"] == 1
    assert cliente["origen_clave"] == auth.ORIGEN_HABILITADA
    assert auth.buscar_cliente("333")["contrasena_cifrada"] == "cif1"


def test_migracion_tabla_vieja_con_clave_not_null():
    auth.DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(auth.DB_PATH)
    conn.execute(
        """
        CREATE TABLE clientes_autorizados (
            numero_documento TEXT PRIMARY KEY,
            tipo_documento TEXT NOT NULL DEFAULT 'Cédula de Ciudadanía',
            contrasena_cifrada TEXT NOT NULL,
            fecha_vencimiento TEXT,
            telefono TEXT,
            contrasena_propia INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO clientes_autorizados VALUES "
        "('111', 'CC', 'cifrado', '2026-01-01', '3001234567', 1, "
        "'2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    auth.iniciar_db()

    assert _columnas_clientes()["contrasena_cifrada"]["notnull"] == 0
    cliente = auth.listar_clientes()[0]
    assert cliente["numero_documento"] == "111"
    assert cliente["tiene_clave"] == 1
    assert cliente["origen_clave"] == auth.ORIGEN_MANUAL
    assert cliente["telefono"] == "3001234567"

    # La tabla reconstruida ya acepta clientes sin clave.
    auth.guardar_cliente("222", "CC", None, None)
    assert auth.cliente_tiene_clave("222") is False