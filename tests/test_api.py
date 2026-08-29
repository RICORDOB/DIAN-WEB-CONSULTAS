"""Smoke de la API web: flujo de alta→aprobación→bloqueo, revocación en vivo,
dashboard del admin y seguridad básica, usando TestClient."""

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import _ratelimit, app


@pytest.fixture(autouse=True)
def _limpiar_ratelimit():
    _ratelimit.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(client, usuario, password):
    r = client.post("/api/login", json={"usuario": usuario, "password": password})
    return r


def test_flujo_alta_aprobacion_bloqueo_revocacion(client, db, admin):
    # Alta queda pendiente
    r = client.post("/api/registro", json={"usuario": "pedro", "password": "clave123"})
    assert r.status_code == 200 and r.json()["estado"] == "pendiente"
    assert _login(client, "pedro", "clave123").status_code == 401  # pendiente

    # Admin aprueba
    admin_login = _login(client, admin, "admin123")
    assert admin_login.status_code == 200
    cookie = admin_login.cookies.get("sesion")
    r = client.get("/api/admin/pendientes", cookies={"sesion": "token-invalido"})
    assert r.status_code == 403
    r = client.get("/api/admin/pendientes", cookies={"sesion": cookie})
    assert r.status_code == 200
    assert any(u["usuario"] == "pedro" for u in r.json()["pendientes"])
    r = client.post("/api/admin/decidir", json={"usuario": "pedro", "aprobar": True},
                    cookies={"sesion": cookie})
    assert r.status_code == 200

    # Usuario ya puede entrar
    pedro = _login(client, "pedro", "clave123")
    assert pedro.status_code == 200
    pedro_cookie = pedro.cookies.get("sesion")

    # Bloqueo: derriba la sesión en la siguiente petición y prohíbe el login
    r = client.post("/api/admin/bloquear", json={"usuario": "pedro", "bloquear": True},
                    cookies={"sesion": cookie})
    assert r.status_code == 200 and r.json()["estado"] == "bloqueado"
    r = client.get("/api/me", cookies={"sesion": pedro_cookie})
    assert r.json()["autenticado"] is False  # revocación en vivo
    assert _login(client, "pedro", "clave123").status_code == 401

    # Desbloqueo: vuelve a entrar
    r = client.post("/api/admin/bloquear", json={"usuario": "pedro", "bloquear": False},
                    cookies={"sesion": cookie})
    assert r.status_code == 200
    assert _login(client, "pedro", "clave123").status_code == 200


def test_admin_no_puede_bloquearse(client, db, admin):
    cookie = _login(client, admin, "admin123").cookies.get("sesion")
    r = client.post("/api/admin/bloquear", json={"usuario": admin, "bloquear": True},
                    cookies={"sesion": cookie})
    assert r.status_code == 400


def test_usuarios_comunes_no_acceden_al_panel(client, db, admin):
    cookie = _login(client, admin, "admin123").cookies.get("sesion")
    client.post("/api/registro", json={"usuario": "laura", "password": "clave123"})
    client.post("/api/admin/decidir", json={"usuario": "laura", "aprobar": True},
                cookies={"sesion": cookie})
    laura = _login(client, "laura", "clave123").cookies.get("sesion")
    assert client.get("/api/admin/estadisticas",
                      cookies={"sesion": laura}).status_code == 403
    assert client.get("/api/admin/consultas",
                      cookies={"sesion": laura}).status_code == 403


def test_estadisticas_y_historial(client, db, admin):
    cookie = _login(client, admin, "admin123").cookies.get("sesion")
    auth.registrar_consulta("job-abc", "pedro", "Cédula de Ciudadanía")
    auth.actualizar_consulta("job-abc", auth.ESTADO_DONE, resultado="PEDRO.xls")

    r = client.get("/api/admin/estadisticas", cookies={"sesion": cookie})
    assert r.status_code == 200
    data = r.json()
    assert data["consultas"]["total"] == 1
    assert data["consultas"]["done"] == 1
    assert data["por_dia"][-1]["total"] >= 1

    r = client.get("/api/admin/consultas", cookies={"sesion": cookie})
    assert r.status_code == 200
    filas = r.json()["consultas"]
    assert len(filas) == 1 and filas[0]["usuario"] == "pedro"
    assert "numero_documento" not in filas[0]


def test_consulta_sin_sesion_rechazada(client, db):
    r = client.post("/api/consulta", json={
        "tipo_documento": "Cédula de Ciudadanía",
        "numero_documento": "12345678",
        "contrasena": "x",
    })
    assert r.status_code == 401


def test_headers_de_seguridad(client, db):
    r = client.get("/")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "same-origin"