"""Pruebas del issue #4: fecha de evaluacion y meses de afiliacion.

Convencion: un mes se cumple el mismo dia del mes siguiente; si ese dia no
existe (inicio el 31 y el mes tiene menos dias), se cumple el ultimo dia del mes.

Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from datetime import date

import pytest

from preauth import reglas
from preauth.reglas import Decision, InformeMedico, Poliza, Resultado, evaluar, meses_afiliado

HOY = date(2026, 9, 26)
DOCS_COMPLETOS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]


def poliza(fecha_inicio=date(2024, 1, 1), **cambios) -> Poliza:
    datos = dict(
        paciente_id="P001",
        cobertura_procedimientos=["Colecistectomía"],
        fecha_inicio=fecha_inicio,
        carencia_meses={"default": 8},
        monto_maximo=50000,
        monto_usado=5000,
    )
    datos.update(cambios)
    return Poliza(**datos)


def informe(**cambios) -> InformeMedico:
    datos = dict(
        paciente_id="P001",
        procedimiento="Colecistectomía",
        diagnostico_cie10="K80",
        medico="Dr. Gil",
        documentos_adjuntos=list(DOCS_COMPLETOS),
        costo_estimado=8000,
    )
    datos.update(cambios)
    return InformeMedico(**datos)


def resultados_por_regla(r) -> dict:
    return {h.regla: h.resultado for h in r["hallazgos"]}


# --- meses_afiliado() -------------------------------------------------------

@pytest.mark.parametrize("inicio, hoy, esperado", [
    (date(2026, 1, 31), date(2026, 2, 1), 0),    # un dia no es un mes
    (date(2026, 1, 15), date(2026, 9, 15), 8),   # mismo dia: mes cumplido
    (date(2026, 1, 15), date(2026, 9, 14), 7),   # un dia antes: aun no
    (date(2026, 1, 31), date(2026, 2, 27), 0),   # febrero, antes del ultimo dia
    (date(2026, 1, 31), date(2026, 2, 28), 1),   # febrero no tiene 31: se cumple el 28
    (date(2024, 1, 31), date(2024, 2, 29), 1),   # bisiesto: se cumple el 29
    (date(2026, 3, 10), date(2026, 3, 10), 0),   # inicia hoy
    (date(2024, 1, 1), date(2026, 9, 26), 32),   # caso de la demo, sin cambios
    (date(2026, 10, 1), date(2026, 9, 26), 0),   # poliza futura: nunca negativo
])
def test_meses_afiliado(inicio, hoy, esperado):
    assert meses_afiliado(poliza(fecha_inicio=inicio), hoy) == esperado


# --- evaluar(): vigencia y carencia -----------------------------------------

def test_poliza_que_inicia_en_el_futuro_va_a_revision_manual():
    r = evaluar(poliza(fecha_inicio=date(2026, 10, 1)), informe(), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL
    reglas_ = resultados_por_regla(r)
    assert reglas_["vigencia"] is Resultado.REVISION
    # Sin vigencia no se evalua la carencia (si no, DENEGADA ganaria la precedencia).
    assert "carencia" not in reglas_
    assert "autorizacion_id" not in r


def test_poliza_futura_en_emergencia_tambien_va_a_revision_manual():
    r = evaluar(poliza(fecha_inicio=date(2026, 10, 1)), informe(urgencia="emergencia"), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL


def test_poliza_que_inicia_hoy_esta_vigente():
    r = evaluar(poliza(fecha_inicio=HOY, carencia_meses={"default": 0}), informe(), HOY)
    assert r["decision"] is Decision.PREAPROBADA
    assert resultados_por_regla(r)["vigencia"] is Resultado.CUMPLE


def test_carencia_cuenta_meses_completos_no_meses_de_calendario():
    # 26-ene -> 25-sep: 7 meses completos (antes se contaban 8 y se aprobaba).
    r = evaluar(poliza(fecha_inicio=date(2026, 1, 26)), informe(), date(2026, 9, 25))
    assert r["decision"] is Decision.DENEGADA
    assert r["motivo"] == "No cumple carencia: 7/8 meses."


# --- evaluar(): fecha por defecto --------------------------------------------

class _FechaFija(date):
    """date cuyo today() devuelve siempre el mismo dia."""

    @classmethod
    def today(cls):
        return cls(2030, 1, 15)


def test_sin_hoy_explicito_se_usa_la_fecha_del_momento_de_la_llamada(monkeypatch):
    # Antes, date.today() se evaluaba UNA vez al importar el modulo.
    # Desde #44 la fecha sale de hoy_local() (zona de Panama); se fija esa funcion.
    monkeypatch.setattr(reglas, "hoy_local", _FechaFija.today)
    r = evaluar(poliza(), informe())
    assert r["autorizacion_id"] == "AUT-P001-20300115"
