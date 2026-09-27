"""Pruebas del issue #44: la fecha de evaluacion es la de Panama, no la del servidor (UTC).

Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from datetime import date, datetime, timezone

import pytest

from preauth import config, reglas
from preauth.config import ConfigError
from preauth.reglas import InformeMedico, Poliza, evaluar, hoy_local


@pytest.fixture(autouse=True)
def zona_limpia():
    """La zona se guarda en cache: cada prueba la vuelve a leer del entorno."""
    config.zona_horaria.cache_clear()
    yield
    config.zona_horaria.cache_clear()


def utc(anio, mes, dia, hora, minuto=0) -> datetime:
    return datetime(anio, mes, dia, hora, minuto, tzinfo=timezone.utc)


@pytest.mark.parametrize("ahora, esperado", [
    (utc(2026, 9, 27, 2, 0), date(2026, 9, 26)),    # 21:00 del 26 en Panama
    (utc(2026, 9, 27, 4, 59), date(2026, 9, 26)),   # 23:59 del 26 en Panama
    (utc(2026, 9, 27, 5, 0), date(2026, 9, 27)),    # 00:00 del 27 en Panama
    (utc(2026, 9, 26, 15, 0), date(2026, 9, 26)),   # 10:00, mismo dia en ambas zonas
])
def test_hoy_local_usa_la_hora_de_panama(ahora, esperado):
    assert hoy_local(ahora) == esperado


def test_la_zona_es_configurable(monkeypatch):
    monkeypatch.setenv("ZONA_HORARIA", "Asia/Tokyo")   # UTC+9
    assert hoy_local(utc(2026, 9, 26, 16, 0)) == date(2026, 9, 27)


def test_una_zona_invalida_se_rechaza_con_mensaje_claro(monkeypatch):
    monkeypatch.setenv("ZONA_HORARIA", "Panama/Inventada")
    with pytest.raises(ConfigError):
        config.zona_horaria()


def test_evaluar_sin_hoy_usa_la_fecha_local(monkeypatch):
    # 21:00 del 26 en Panama (02:00 UTC del 27): la autorizacion debe llevar el 26.
    monkeypatch.setattr(reglas, "hoy_local", lambda: date(2026, 9, 26))
    poliza = Poliza("P001", ["Colecistectomía"], date(2024, 1, 1), {"default": 8}, 50000, 5000)
    informe = InformeMedico("P001", "Colecistectomía", "K80", "Dr. Gil",
                            documentos_adjuntos=["identificacion", "informe_medico", "consentimiento",
                                                 "ecografia_abdominal", "analitica"],
                            costo_estimado=8000)
    assert evaluar(poliza, informe)["autorizacion_id"] == "AUT-P001-20260926"
