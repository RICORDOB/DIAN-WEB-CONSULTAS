"""Configuración común de los tests: BD aislada en un directorio temporal."""

import os
import tempfile
from pathlib import Path

import pytest

# Antes de importar la app, fijamos un entorno de prueba aislado.
_TMP = tempfile.mkdtemp(prefix="dian_tests_")
os.environ["APP_DATA_DIR"] = _TMP
os.environ["APP_JOBS_DIR"] = str(Path(_TMP) / "jobs")
os.environ["APP_SECRET_KEY"] = "clave-de-prueba-segura"
os.environ["APP_ENV"] = "dev"
os.environ.pop("TURSO_DB_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)

from app import auth  # noqa: E402
from app.runner import DianRunner  # noqa: E402


@pytest.fixture()
def db():
    """Base de datos limpia en cada test."""
    auth.DB_PATH.unlink(missing_ok=True)
    auth.iniciar_db()
    yield


@pytest.fixture()
def admin(db):
    """Usuarios: crea un administrador a partir de variables de entorno."""
    os.environ["APP_ADMIN_USER"] = "admin"
    os.environ["APP_ADMIN_PASS"] = "admin123"
    auth.iniciar_db()
    return os.environ["APP_ADMIN_USER"]


@pytest.fixture()
def test_dir(tmp_path: Path) -> Path:
    """Directorio de trabajo aislado para el runner."""
    return tmp_path


@pytest.fixture()
def runner(test_dir: Path) -> DianRunner:
    return DianRunner(job_dir=test_dir)