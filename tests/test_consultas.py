"""Pruebas del historial de consultas (registro, actualización, huérfanas y métricas)."""

from app import auth


def test_registrar_y_actualizar(db):
    auth.registrar_consulta("job1", "juan", "Cédula de Ciudadanía")
    auth.actualizar_consulta("job1", auth.ESTADO_DONE, resultado="JUAN.xls")
    rows = auth.listar_consultas()
    assert len(rows) == 1
    assert rows[0]["id"] == "job1"
    assert rows[0]["estado"] == auth.ESTADO_DONE
    assert rows[0]["resultado"] == "JUAN.xls"
    # No se persiste el número de documento de los clientes
    assert "numero_documento" not in rows[0]


def test_estadisticas(db):
    auth.registrar_consulta("job1", "juan", "Cédula de Ciudadanía")
    auth.registrar_consulta("job2", "juan", "NIT")
    auth.registrar_consulta("job3", "maria", "Cédula de Ciudadanía")
    auth.actualizar_consulta("job1", auth.ESTADO_DONE, resultado="A.xls")
    auth.actualizar_consulta("job3", auth.ESTADO_ERROR, error="boom")

    est = auth.estadisticas_completas()
    assert est["consultas"]["total"] == 3
    assert est["consultas"]["done"] == 1
    assert est["consultas"]["error"] == 1
    assert est["consultas"]["queued"] == 1  # job2 quedó en cola
    por_usuario = {p["usuario"]: p["total"] for p in est["por_usuario"]}
    assert por_usuario.get("juan") == 2
    # Último día de la serie debe ser hoy (los tests corren "hoy")
    assert est["por_dia"][-1]["fecha"] == auth.date.today().isoformat()


def test_filtros_de_listado(db):
    auth.registrar_consulta("job1", "juan", "CC")
    auth.registrar_consulta("job2", "maria", "NIT")
    assert len(auth.listar_consultas(usuario="juan")) == 1
    assert len(auth.listar_consultas(usuario="juan", estado=auth.ESTADO_QUEUED)) == 1
    assert len(auth.listar_consultas(estado="done")) == 0


def test_huerfanas_se_marcan_error(db):
    auth.registrar_consulta("job1", "juan", "CC")
    auth.actualizar_consulta("job1", auth.ESTADO_RUNNING)
    auth.registrar_consulta("job2", "juan", "CC")
    auth.registrar_consulta("job3", "juan", "CC")
    auth.actualizar_consulta("job3", auth.ESTADO_DONE, resultado="C.xls")

    marcadas = auth.marcar_consultas_huerfanas()
    assert marcadas == 2
    filas = {r["id"]: r for r in auth.listar_consultas()}
    assert filas["job1"]["estado"] == auth.ESTADO_ERROR
    assert filas["job2"]["estado"] == auth.ESTADO_ERROR
    assert filas["job3"]["estado"] == auth.ESTADO_DONE