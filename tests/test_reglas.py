"""Pruebas del motor de reglas con los casos documentados en el README.

La fecha se fija para que el resultado no dependa del dia en que corre CI.
Los casos borde de reglas se agregan cuando cierren los issues de reglas (#3, #4, #5).
"""
from datetime import date

from preauth.reglas import Decision, InformeMedico, Poliza, evaluar

HOY = date(2026, 9, 26)
DOCS_COMPLETOS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]


def poliza(fecha_inicio=date(2024, 1, 1)):
    return Poliza("P001", ["Colecistectomía"], fecha_inicio, {"default": 8}, 50000, 5000)


def informe(procedimiento="Colecistectomía", docs=DOCS_COMPLETOS, costo=8000, urgencia="programada"):
    return InformeMedico("P001", procedimiento, "K80", "Dr. Gil", urgencia=urgencia,
                         documentos_adjuntos=list(docs), costo_estimado=costo)


def test_documentacion_completa_se_preaprueba():
    r = evaluar(poliza(), informe(), HOY)
    assert r["decision"] == Decision.PREAPROBADA
    assert r["autorizacion_id"] == "AUT-P001-20260926"


def test_faltan_documentos():
    r = evaluar(poliza(), informe(docs=["identificacion", "informe_medico"]), HOY)
    assert r["decision"] == Decision.DOCUMENTOS_FALTANTES
    assert sorted(r["faltantes"]) == ["analitica", "consentimiento", "ecografia_abdominal"]


def test_procedimiento_no_cubierto_se_deniega():
    r = evaluar(poliza(), informe(procedimiento="Rinoplastia estética"), HOY)
    assert r["decision"] == Decision.DENEGADA


def test_carencia_no_cumplida_se_deniega():
    # 5 meses de afiliacion, la poliza exige 8
    r = evaluar(poliza(fecha_inicio=date(2026, 4, 26)), informe(), HOY)
    assert r["decision"] == Decision.DENEGADA
    assert "5/8" in r["motivo"]


def test_emergencia_omite_carencia():
    r = evaluar(poliza(fecha_inicio=date(2026, 4, 26)), informe(urgencia="emergencia"), HOY)
    assert r["decision"] == Decision.PREAPROBADA


def test_monto_excedido_se_deniega():
    r = evaluar(poliza(), informe(costo=46000), HOY)
    assert r["decision"] == Decision.DENEGADA
