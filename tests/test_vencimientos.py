"""Pruebas de los avisos de vencimiento: lógica de fechas y orquestador."""

from datetime import date, datetime

from app import auth, vencimientos

TZ = vencimientos.TZ_BOGOTA


def _cliente(numero: str, fecha):
    return {"numero_documento": numero, "fecha_vencimiento": fecha}


# ---------------------------------------------------------------------------
# avisos_del_dia (lógica pura)
# ---------------------------------------------------------------------------
def test_aviso_el_dia_del_vencimiento():
    clientes = [_cliente("9001", "2026-10-22")]
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 22))
    assert avisos["hoy"] == ["9001"]
    assert avisos["semana"] == [] and avisos["cinco"] == []


def test_venc_lunes_avisa_hoy_y_no_semana():
    clientes = [_cliente("9002", "2026-10-26")]  # lunes
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 26))
    assert avisos["hoy"] == ["9002"]
    assert avisos["semana"] == []


def test_venc_lunes_avisa_cinco_dias_antes():
    clientes = [_cliente("9002", "2026-10-26")]
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 21))
    assert avisos["cinco"] == ["9002"]


def test_venc_sabado_el_lunes_avisa_semana_y_no_cinco():
    clientes = [_cliente("9003", "2026-10-17")]  # sábado; lunes = 12; cinco = 12
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 12))
    assert avisos["semana"] == ["9003"]
    assert avisos["cinco"] == []


def test_venc_domingo_omite_el_aviso_de_cinco_dias():
    clientes = [_cliente("9004", "2026-10-18")]  # domingo; lunes = 12; cinco = 13
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 12))
    assert avisos["semana"] == ["9004"]
    # El martes 13 (venc-5) ya no corresponde: el lunes avisó primero.
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 13))
    assert avisos == {"hoy": [], "semana": [], "cinco": []}


def test_venc_jueves_los_tres_avisos_en_orden():
    clientes = [_cliente("9005", "2026-10-22")]  # jueves
    assert vencimientos.avisos_del_dia(clientes, date(2026, 10, 17))["cinco"] == ["9005"]
    assert vencimientos.avisos_del_dia(clientes, date(2026, 10, 19))["semana"] == ["9005"]
    assert vencimientos.avisos_del_dia(clientes, date(2026, 10, 22))["hoy"] == ["9005"]


def test_fechas_invalidas_y_vacias_se_ignoran():
    clientes = [
        _cliente("9006", "2026-13-40"),
        _cliente("9007", ""),
        _cliente("9008", None),
        _cliente("9009", "sin-fecha"),
        {"numero_documento": "", "fecha_vencimiento": "2026-10-22"},
        _cliente("9010", date(2026, 10, 22)),  # un date directo también vale
    ]
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 22))
    assert avisos["hoy"] == ["9010"]


def test_varios_clientes_se_agrupan():
    clientes = [_cliente("9001", "2026-10-22"), _cliente("9002", "2026-10-22"),
                _cliente("9003", "2026-10-23")]
    avisos = vencimientos.avisos_del_dia(clientes, date(2026, 10, 22))
    assert avisos["hoy"] == ["9001", "9002"]


def test_textos_aviso_resumen():
    hoy = date(2026, 10, 22)
    titulo, cuerpo = vencimientos.textos_aviso("hoy", ["9001"], hoy)
    assert "HOY" in titulo and "1 cédula" in cuerpo and "jue 22 oct" in cuerpo

    numeros = [str(9000 + i) for i in range(6)]
    _, cuerpo = vencimientos.textos_aviso("semana", numeros, hoy)
    assert "6 cédulas" in cuerpo and "y 2 más" in cuerpo


# ---------------------------------------------------------------------------
# revisar_y_notificar (orquestador)
# ---------------------------------------------------------------------------
def _mock_push(monkeypatch):
    llamadas = []

    def fake(usuario, titulo, cuerpo, url, tag):
        llamadas.append({"usuario": usuario, "titulo": titulo, "cuerpo": cuerpo,
                         "url": url, "tag": tag})
        return 1

    monkeypatch.setattr(vencimientos.push, "notificar", fake)
    return llamadas


def test_antes_de_las_8_no_hace_nada(db, monkeypatch):
    llamadas = _mock_push(monkeypatch)
    res = vencimientos.revisar_y_notificar(
        ahora=datetime(2026, 10, 22, 7, 0, tzinfo=TZ))
    assert "omitido" in res
    assert llamadas == []


def test_envia_marca_y_no_repite(db, monkeypatch):
    llamadas = _mock_push(monkeypatch)
    auth.guardar_suscripcion("ana", "https://fcm/x", "k", "a")
    auth.guardar_cliente("9001", "CC", "cifrado", "2026-10-22")
    auth.guardar_cliente("9002", "CC", "cifrado", "2026-11-05")

    res = vencimientos.revisar_y_notificar(
        ahora=datetime(2026, 10, 22, 8, 0, tzinfo=TZ))
    assert res["enviados"] == 1
    assert res["avisos"]["hoy"] == 1
    assert len(llamadas) == 1
    assert llamadas[0]["usuario"] == "ana"
    assert llamadas[0]["url"] == "/dev"
    assert llamadas[0]["tag"] == "vencimientos"

    # Segundo chequeo del mismo día: omitido por el marcador.
    res2 = vencimientos.revisar_y_notificar(
        ahora=datetime(2026, 10, 22, 9, 30, tzinfo=TZ))
    assert res2.get("omitido") == "ya ejecutado hoy"
    assert len(llamadas) == 1

    # Forzado (CLI): ignora el marcador y vuelve a enviar.
    res3 = vencimientos.revisar_y_notificar(
        ahora=datetime(2026, 10, 22, 9, 31, tzinfo=TZ), forzar=True)
    assert res3["enviados"] == 1
    assert len(llamadas) == 2


def test_sin_suscritos_no_marca_el_dia(db, monkeypatch):
    _mock_push(monkeypatch)
    res = vencimientos.revisar_y_notificar(
        ahora=datetime(2026, 10, 22, 8, 0, tzinfo=TZ))
    assert res["suscritos"] == 0
    assert auth.config_get(vencimientos.CLAVE_ULTIMA_EJECUCION) is None
