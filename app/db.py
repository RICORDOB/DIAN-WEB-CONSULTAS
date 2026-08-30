"""
db — Capa de persistencia con dos backends intercambiables.

  * Turso (libSQL en la nube): se activa si existen TURSO_DB_URL y
    TURSO_AUTH_TOKEN. Usa el protocolo HTTP v2 de libSQL (POST /v2/pipeline)
    con la librería estándar (urllib/json); no añade dependencias. Las
    transacciones viajan en un stream identificado por un ``baton`` que el
    servidor renueva en cada respuesta y hay que re-enviar en la siguiente.
  * sqlite3 local: fallback cuando no hay credenciales Turso (desarrollo
    y pruebas sin red).

La interfaz imita la parte de sqlite3 que usa la app: conexión que entra
en un ``with`` (commit/rollback), método ``execute(sql, params)`` que
devuelve un cursor con ``fetchone/fetchall/rowcount``, y filas accesibles
por nombre, por índice, ``dict(fila)`` y ``dict([fila, fila])``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import ssl
import urllib.error
import urllib.request
from typing import Any, Iterator, Sequence


def _credenciales() -> tuple[str, str]:
    return os.environ.get("TURSO_DB_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", "")


def turso_activado() -> bool:
    """True cuando hay credenciales Turso para usar el backend HTTP."""
    url, token = _credenciales()
    return bool(url and token)


class LibsqlError(Exception):
    """Error remoto de libSQL con el mensaje del servidor."""


# ---------------------------------------------------------------------------
# Fila compatible con sqlite3.Row (acceso por nombre e índice, dict, iterable)
# ---------------------------------------------------------------------------
class Fila:
    __slots__ = ("_columnas", "_valores")

    def __init__(self, columnas: list[str], valores: list[Any]) -> None:
        self._columnas = list(columnas)
        self._valores = list(valores)

    def keys(self) -> list[str]:
        return self._columnas

    def __getitem__(self, item):
        if isinstance(item, str):
            return self._valores[self._columnas.index(item)]
        return self._valores[item]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._valores)

    def __len__(self) -> int:
        return len(self._valores)

    def __eq__(self, otro) -> bool:
        if isinstance(otro, Fila):
            return self._valores == otro._valores
        if isinstance(otro, (tuple, list)):
            return self._valores == list(otro)
        return NotImplemented

    def __repr__(self) -> str:
        return f"<Fila {dict(zip(self._columnas, self._valores))}>"


def _valor_celda(celda: Any) -> Any:
    """Convierte una celda tipada de libSQL ({type, value}) a Python."""
    if isinstance(celda, dict):
        tipo, valor = celda.get("type"), celda.get("value")
        if tipo == "integer":
            return int(valor) if valor is not None else None
        if tipo == "real":
            return float(valor) if valor is not None else None
        if tipo == "null" or valor is None:
            return None
        return valor
    return celda


def _filas(meta: dict) -> list[Fila]:
    celdas = meta.get("cols", meta.get("columns", []))
    cols = [c["name"] if isinstance(c, dict) else str(c) for c in celdas]
    return [Fila(cols, [_valor_celda(v) for v in fila])
            for fila in meta.get("rows", [])]


class _Cursor:
    """Cursor mínimo: fetchone/fetchall + rowcount (afectadas por la última
    sentencia) + lastrowid."""

    __slots__ = ("_filas", "rowcount", "lastrowid")

    def __init__(self, filas: list[Fila], affected: int = -1,
                 lastrowid: int | None = None) -> None:
        self._filas = filas
        self.rowcount = affected
        self.lastrowid = lastrowid

    def fetchone(self) -> Fila | None:
        return self._filas[0] if self._filas else None

    def fetchall(self) -> list[Fila]:
        return self._filas


# ---------------------------------------------------------------------------
# Cliente HTTP de libSQL v2 (protocolo pipeline con baton)
# ---------------------------------------------------------------------------
class _TransporteHTTP:
    """Hace una petición POST por operación al endpoint /v2/pipeline."""

    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        if base_url.startswith("libsql://"):
            base_url = "https://" + base_url[len("libsql://"):]
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _pedir(self, requests: list[dict], baton: str | None = None) -> dict:
        payload: dict = {"requests": requests}
        if baton:
            payload["baton"] = baton
        req = urllib.request.Request(
            f"{self.base_url}/v2/pipeline",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LibsqlError(f"HTTP {exc.code}: {exc.read()[:300]!r}") from exc
        except urllib.error.URLError as exc:
            raise LibsqlError(f"Sin conexión a Turso: {exc.reason}") from exc

    @staticmethod
    def _parsear(resultado: dict) -> dict:
        """Valida un resultado del pipeline; devuelve el resultset de ejecución."""
        if resultado.get("type") == "error":
            err = resultado.get("error", {})
            raise LibsqlError(err.get("message", "error remoto de libSQL"))
        respuesta = resultado.get("response", {})
        if respuesta.get("type") == "close":
            return {}
        return respuesta.get("result", {}) or {}

    def ejecutar(self, sql: str, params: Sequence = (),
                 baton: str | None = None) -> tuple[_Cursor, str | None]:
        stmt: dict = {"sql": sql}
        if params:
            stmt["args"] = [{"type": "text", "value": str(v)} for v in params]
        crudo = self._pedir([{"type": "execute", "stmt": stmt}], baton=baton)
        meta = self._parsear(crudo["results"][0])
        filas = _filas(meta) if "rows" in meta else []
        afectadas = meta.get("affected_row_count", meta.get("rows_written"))
        if afectadas is None:
            afectadas = len(filas)
        lastrowid = meta.get("last_insert_rowid")
        lastid = int(lastrowid) if lastrowid is not None else None
        return _Cursor(filas, affected=afectadas, lastrowid=lastid), crudo.get("baton")

    def transaccion(self, sql: str, baton: str | None = None) -> str | None:
        """Ejecuta una instrucción de transacción (BEGIN/COMMIT/ROLLBACK).
        Devuelve el baton a re-enviar en la siguiente petición."""
        crudo = self._pedir([{"type": "execute", "stmt": {"sql": sql}}], baton=baton)
        self._parsear(crudo["results"][0])
        return crudo.get("baton")

    def cerrar_baton(self, baton: str | None) -> None:
        if baton:
            self._pedir([{"type": "close"}], baton=baton)


class ConexionTurso:
    """Conexión que mantiene el baton de transacción mientras vive el ``with``."""

    def __init__(self, transporte: _TransporteHTTP) -> None:
        self._t = transporte
        self._baton: str | None = None

    def __enter__(self) -> "ConexionTurso":
        self._baton = self._t.transaccion("BEGIN")
        return self

    def __exit__(self, et, ev, tb) -> None:
        try:
            if et is None:
                self._baton = self._t.transaccion("COMMIT", self._baton)
            else:
                self._baton = self._t.transaccion("ROLLBACK", self._baton)
        finally:
            self._t.cerrar_baton(self._baton)
            self._baton = None

    def execute(self, sql: str, params: Sequence = ()) -> _Cursor:
        cur, baton = self._t.ejecutar(sql, params, baton=self._baton)
        if baton is not None:
            # El servidor renueva el baton en cada respuesta.
            self._baton = baton
        return cur


# ---------------------------------------------------------------------------
# Backend local (sqlite3) con la misma interfaz
# ---------------------------------------------------------------------------
class _ConexionSqlite:
    """Envuelve sqlite3.Connection: mismo execute() y contexto commit/rollback."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def __enter__(self) -> "_ConexionSqlite":
        return self

    def __exit__(self, et, ev, tb) -> None:
        if et is None:
            self._conn.commit()
        else:
            self._conn.rollback()

    def execute(self, sql: str, params: Sequence = ()):
        return self._conn.execute(sql, params)


def conectar():
    """Devuelve una conexión nueva al backend activo (Turso o sqlite3 local)."""
    url, token = _credenciales()
    if url and token:
        return ConexionTurso(_TransporteHTTP(url, token))
    data_dir = os.environ.get("APP_DATA_DIR", "data")
    return _ConexionSqlite(os.path.join(data_dir, "usuarios.db"))