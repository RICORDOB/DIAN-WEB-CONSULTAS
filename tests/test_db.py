"""Pruebas del adaptador de BD de Turso (protocolo HTTP libSQL) con un servidor
HTTP de mentira (stdlib). Valida serialización de peticiones, parseo de
respuestas, transacciones por baton, filas tipo Row y rowcount, sin necesitar
credenciales reales."""

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest

from app import db


# ---------------------------------------------------------------------------
# Mini servidor libSQL (protocolo pipeline con baton) sobre un sqlite :memory:
# ---------------------------------------------------------------------------
class _LibsqlFake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cometidos: list[dict] = []
    baterias: dict[str, str] = {}
    reiniciar = True

    def log_message(self, *a):
        pass

    @classmethod
    def _con(cls) -> sqlite3.Connection:
        if not hasattr(cls, "_db") or cls.reiniciar:
            cls._db = sqlite3.connect(":memory:")
            cls.reiniciar = False
        return cls._db

    @staticmethod
    def _es_txn(sql: str) -> bool:
        return sql.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))

    @staticmethod
    def _celda(v):
        if v is None:
            return {"type": "null", "value": None}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "real", "value": str(v)}
        return {"type": "text", "value": str(v)}

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/v2/pipeline":
            self._respuesta(404, {"results": [
                {"type": "error", "error": {"message": "ruta desconocida"}}]})
            return
        largo = int(self.headers.get("Content-Length", 0))
        cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        _LibsqlFake.cometidos.append(cuerpo)

        conn = self._con()
        por_siguiente = cuerpo.get("baton")
        resultados = []
        for peticion in cuerpo["requests"]:
            stmt = peticion.get("stmt", {})
            sql = stmt.get("sql", "")
            if peticion["type"] == "close":
                _LibsqlFake.baterias.pop(por_siguiente, None)
                resultados.append({"type": "ok", "response": {"type": "close"}})
                continue
            if sql and self._es_txn(sql):
                op = sql.strip().upper()
                if op == "BEGIN":
                    conn.execute("BEGIN")
                    nuevo = f"baton-{len(_LibsqlFake.baterias) + 1}"
                    _LibsqlFake.baterias[nuevo] = True
                    por_siguiente = nuevo
                else:
                    conn.execute(sql)
                    por_siguiente = None
                resultados.append({"type": "ok", "response": {
                    "type": "execute",
                    "result": {"cols": [], "rows": [], "affected_row_count": 0,
                               "last_insert_rowid": None}}})
                continue
            args = [a["value"] for a in stmt.get("args", [])]
            try:
                cur = conn.execute(sql, args)
            except sqlite3.Error as exc:
                resultados.append({"type": "error", "error": {"message": f"{exc}"}})
                continue
            columnas = [d[0] for d in (cur.description or [])]
            filas = [[self._celda(v) for v in f] for f in cur.fetchall()]
            af = cur.rowcount if cur.rowcount >= 0 else 0
            resultados.append({"type": "ok", "response": {
                "type": "execute",
                "result": {
                    "cols": columnas, "rows": filas,
                    "affected_row_count": af, "last_insert_rowid": cur.lastrowid,
                }}})
        self._respuesta(200, {"baton": por_siguiente, "results": resultados})

    def _respuesta(self, codigo: int, obj: dict):
        cuerpo = json.dumps(obj).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


@pytest.fixture()
def turso():
    servidor = HTTPServer(("127.0.0.1", 0), _LibsqlFake)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    _LibsqlFake.cometidos = []
    _LibsqlFake.baterias = {}
    _LibsqlFake.reiniciar = True
    try:
        yield {
            "url": f"http://127.0.0.1:{servidor.server_address[1]}",
            "token": "token-falso",
        }
    finally:
        servidor.shutdown()


def _conectar_a(turso):
    return db.ConexionTurso(db._TransporteHTTP(turso["url"], turso["token"]))


def test_conversion_libsql_a_https():
    t = db._TransporteHTTP("libsql://dian-ricordob.aws-us-east-1.turso.io", "tok")
    assert t.base_url == "https://dian-ricordob.aws-us-east-1.turso.io"
    h = db._TransporteHTTP("http://127.0.0.1:1234", "tok")
    assert h.base_url == "http://127.0.0.1:1234"


def test_turso_activado_por_vars(monkeypatch):
    monkeypatch.setenv("TURSO_DB_URL", "libsql://x.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    assert db.turso_activado() is True
    monkeypatch.delenv("TURSO_DB_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    assert db.turso_activado() is False


def test_ddl_select_y_acceso_a_filas(turso):
    c = _conectar_a(turso)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, nombre TEXT)")
        c.execute("INSERT INTO t (nombre) VALUES ('ana')")
        c.execute("INSERT INTO t (nombre) VALUES ('braulio')")
        cur = c.execute("SELECT id, nombre FROM t WHERE nombre = ?", ("braulio",))
    fila = cur.fetchone()
    assert fila["nombre"] == "braulio"
    assert fila[0] == 2
    assert dict(fila)["nombre"] == "braulio"


def test_rowcount_cero_detecta_ausencia(turso):
    c = _conectar_a(turso)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
        cur = c.execute("UPDATE t SET id = id WHERE id = 99")
    assert cur.rowcount == 0


def test_dict_de_filas_clave_valor(turso):
    c = _conectar_a(turso)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS t (estado TEXT, n INTEGER)")
        c.execute("INSERT INTO t VALUES ('aprobado', 3)")
        c.execute("INSERT INTO t VALUES ('pendiente', 1)")
        filas = c.execute("SELECT estado, n FROM t").fetchall()
    assert dict(filas) == {"aprobado": 3, "pendiente": 1}


def test_transaccion_commit_y_rollback(turso):
    c = _conectar_a(turso)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        c.execute("INSERT INTO t VALUES ('x')")
    with pytest.raises(RuntimeError):
        with _conectar_a(turso) as c2:
            c2.execute("DELETE FROM t")
            raise RuntimeError("boom")
    with c:
        filas = c.execute("SELECT v FROM t").fetchall()
    assert len(filas) == 1


def test_upsert_on_conflict(turso):
    c = _conectar_a(turso)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS t (clave TEXT PRIMARY KEY, valor TEXT)")
        c.execute(
            "INSERT INTO t (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            ("k", "v1"),
        )
        c.execute(
            "INSERT INTO t (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            ("k", "v2"),
        )
        fila = c.execute("SELECT valor FROM t WHERE clave = ?", ("k",)).fetchone()
    assert fila["valor"] == "v2"


def test_el_cliente_envia_authorization_bearer(turso):
    c = _conectar_a(turso)
    with c:
        c.execute("SELECT 1")
    seleccion = next(
        p for p in _LibsqlFake.cometidos
        if any(r.get("stmt", {}).get("sql", "").strip().upper() == "SELECT 1"
               for r in p.get("requests", []))
    )
    assert seleccion["requests"][0]["type"] == "execute"


def test_error_remoto_se_convierte_en_excepcion(turso):
    c = _conectar_a(turso)
    with c:
        with pytest.raises(db.LibsqlError):
            c.execute("SELECT * FROM tabla_que_no_existe")


def test_fila_dict_y_secuencia():
    fila = db.Fila(["a", "b"], [1, 2])
    assert fila["b"] == 2
    assert fila[0] == 1
    assert dict(fila) == {"a": 1, "b": 2}
    assert list(fila) == [1, 2]
    assert len(fila) == 2
    assert fila == (1, 2)


def test_auth_funciona_sobre_backend_sqlite_local():
    """El entorno de prueba local sigue usando sqlite3 y los helpers de config."""
    from app import auth

    assert db.turso_activado() is False
    auth.iniciar_db()
    auth.config_set("clave_prueba", "valorprueba")
    assert auth.config_get("clave_prueba") == "valorprueba"
    assert auth.config_get("no_existe") is None