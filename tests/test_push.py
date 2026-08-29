"""Pruebas de la PWA: suscripciones push (VAPID), manifest y service worker."""

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


def _login(client, usuario, password):
    r = client.post("/api/login", json={"usuario": usuario, "password": password})
    return r


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_push_requiere_sesion(client, db):
    assert client.get("/api/push/clave").status_code == 401
    r = client.post("/api/push/registrar", json={
        "endpoint": "https://fcm.example/sub", "p256dh": "a", "auth": "b"})
    assert r.status_code == 401


def test_registrar_y_eliminar_suscripcion(client, db, admin):
    cookie = _login(client, admin, "admin123").cookies.get("sesion")
    client.post("/api/registro", json={"usuario": "sara", "password": "clave123"})
    client.post("/api/admin/decidir", json={"usuario": "sara", "aprobar": True},
                cookies={"sesion": cookie})
    sara = _login(client, "sara", "clave123").cookies.get("sesion")

    r = client.get("/api/push/clave", cookies={"sesion": sara})
    assert r.status_code == 200 and r.json()["vapid_public_key"]

    endp = "https://fcm.example/sub-abc"
    r = client.post("/api/push/registrar",
                    json={"endpoint": endp, "p256dh": "k1", "auth": "a1"},
                    cookies={"sesion": sara})
    assert r.status_code == 200
    assert auth.listar_suscripciones("sara")[0]["endpoint"] == endp

    # Suscripción incompleta → 400
    r = client.post("/api/push/registrar",
                    json={"endpoint": endp, "p256dh": "", "auth": ""},
                    cookies={"sesion": sara})
    assert r.status_code == 400

    # Baja
    r = client.post("/api/push/eliminar", json={"endpoint": endp},
                    cookies={"sesion": sara})
    assert r.status_code == 200
    assert auth.listar_suscripciones("sara") == []


def test_manifest_y_service_worker_se_sirven(client, db):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert "start_url" in r.text and "icon-192.png" in r.text

    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "push" in r.text and "notificationclick" in r.text

    r = client.get("/assets/icons/icon-192.png")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")