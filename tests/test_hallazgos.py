"""Pruebas del issue #5: todas las reglas se evaluan, se acumulan hallazgos
y la decision final sigue una precedencia fija.

Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from datetime import date

import pytest

from preauth.reglas import (
    Decision, Hallazgo, InformeMedico, Poliza, Resultado, decidir, evaluar,
)

HOY = date(2026, 9, 26)          # con fecha_inicio 2024-01-01 da 32 meses
INICIO_RECIENTE = date(2026, 4, 26)  # 5 meses: no cumple una carencia de 8
DOCS_COMPLETOS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]


def poliza(**cambios) -> Poliza:
    datos = dict(
        paciente_id="P001",
        cobertura_procedimientos=["Colecistectomía"],
        fecha_inicio=date(2024, 1, 1),
        carencia_meses={"default": 8},
        monto_maximo=50000,
        monto_usado=5000,
    )
    datos.update(cambios)
    return Poliza(**datos)


def informe(procedimiento="Colecistectomía", **cambios) -> InformeMedico:
    datos = dict(
        paciente_id="P001",
        procedimiento=procedimiento,
        diagnostico_cie10="K80",
        medico="Dr. Gil",
        documentos_adjuntos=list(DOCS_COMPLETOS),
        costo_estimado=8000,
    )
    datos.update(cambios)
    return InformeMedico(**datos)


def resultados_por_regla(r) -> dict:
    return {h.regla: h.resultado for h in r["hallazgos"]}


# --- Compatibilidad: estos casos ya pasaban y deben seguir IGUAL -------------
# No son pruebas "en rojo": protegen a los consumidores actuales
# (app/main.py, notion_repo.py) de cambios involuntarios.

@pytest.mark.parametrize("inf, decision, motivo, faltantes", [
    (informe(), Decision.PREAPROBADA,
     "Cumple cobertura, carencia y documentación. Pre-aprobación emitida.", []),
    (informe(documentos_adjuntos=["identificacion", "informe_medico"]),
     Decision.DOCUMENTOS_FALTANTES, "Faltan documentos para pre-aprobar.",
     ["analitica", "consentimiento", "ecografia_abdominal"]),
    (informe("Rinoplastia estética", documentos_adjuntos=["identificacion"], costo_estimado=5000),
     Decision.DENEGADA, "Procedimiento 'Rinoplastia estética' no cubierto por póliza.", []),
])
def test_casos_existentes_mantienen_decision_motivo_y_faltantes(inf, decision, motivo, faltantes):
    r = evaluar(poliza(), inf, HOY)
    assert r["decision"] is decision
    assert r["motivo"] == motivo
    assert sorted(r["faltantes"]) == faltantes


def test_autorizacion_solo_si_preaprobada():
    assert evaluar(poliza(), informe(), HOY)["autorizacion_id"] == "AUT-P001-20260926"
    assert "autorizacion_id" not in evaluar(poliza(), informe("Rinoplastia estética"), HOY)


# --- decidir(): precedencia --------------------------------------------------

def h(resultado: Resultado) -> Hallazgo:
    return Hallazgo("prueba", resultado, "mensaje")


@pytest.mark.parametrize("resultados, esperado", [
    ([], Decision.PREAPROBADA),
    ([Resultado.CUMPLE, Resultado.INFORMATIVO], Decision.PREAPROBADA),
    ([Resultado.CUMPLE, Resultado.FALTAN_DOCUMENTOS], Decision.DOCUMENTOS_FALTANTES),
    ([Resultado.FALTAN_DOCUMENTOS, Resultado.REVISION], Decision.REVISION_MANUAL),
    ([Resultado.REVISION, Resultado.NO_CUMPLE, Resultado.FALTAN_DOCUMENTOS], Decision.DENEGADA),
])
def test_decidir_respeta_la_precedencia(resultados, esperado):
    assert decidir([h(r) for r in resultados]) is esperado


# --- evaluar(): comportamiento nuevo ----------------------------------------

def test_se_evaluan_todas_las_reglas_y_se_reportan_varios_hallazgos():
    # Carencia incumplida Y documentos faltantes: antes solo se veia la carencia.
    r = evaluar(poliza(fecha_inicio=INICIO_RECIENTE),
                informe(documentos_adjuntos=["identificacion"]), HOY)
    assert r["decision"] is Decision.DENEGADA
    reglas = resultados_por_regla(r)
    assert reglas["carencia"] is Resultado.NO_CUMPLE
    assert reglas["documentos"] is Resultado.FALTAN_DOCUMENTOS
    assert r["motivo"] == "No cumple carencia: 5/8 meses."
    # Una denegacion no se corrige con documentos: no se piden.
    assert r["faltantes"] == []


def test_procedimiento_vacio_va_a_revision_manual_no_a_denegada():
    r = evaluar(poliza(), informe(""), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL
    assert resultados_por_regla(r)["datos"] is Resultado.REVISION
    assert "cobertura" not in resultados_por_regla(r)


def test_procedimiento_desconocido_va_a_revision_manual():
    r = evaluar(poliza(), informe("desconocido"), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL


def test_revision_manual_tambien_informa_documentos_faltantes():
    r = evaluar(poliza(), informe("", documentos_adjuntos=["identificacion"]), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL
    assert "presupuesto_hospital" in r["faltantes"]


def test_costo_cero_exige_presupuesto_en_lugar_de_pasar_la_regla_de_monto():
    docs_sin_presupuesto = [d for d in DOCS_COMPLETOS if d != "presupuesto_hospital"]
    r = evaluar(poliza(), informe(documentos_adjuntos=docs_sin_presupuesto, costo_estimado=0), HOY)
    assert r["decision"] is Decision.DOCUMENTOS_FALTANTES
    assert r["faltantes"] == ["presupuesto_hospital"]
    assert resultados_por_regla(r)["monto"] is Resultado.FALTAN_DOCUMENTOS


def test_faltantes_salen_ordenados():
    r = evaluar(poliza(), informe(documentos_adjuntos=[]), HOY)
    assert r["faltantes"] == sorted(r["faltantes"])
    assert len(r["faltantes"]) == 5


def test_emergencia_preaprobada_deja_constancia_de_la_carencia_omitida():
    r = evaluar(poliza(fecha_inicio=INICIO_RECIENTE), informe(urgencia="emergencia"), HOY)
    assert r["decision"] is Decision.PREAPROBADA
    assert resultados_por_regla(r)["carencia"] is Resultado.INFORMATIVO
    assert "Emergencia" in r["motivo"]


def test_monto_excedido_con_procedimiento_vacio_sigue_siendo_denegada():
    r = evaluar(poliza(), informe("", costo_estimado=46000), HOY)
    assert r["decision"] is Decision.DENEGADA
    assert r["motivo"] == "Excede monto máximo de póliza."

def test_cada_regla_aporta_un_solo_hallazgo():
    r = evaluar(poliza(), informe(), HOY)
    reglas = [h.regla for h in r["hallazgos"]]
    assert len(reglas) == len(set(reglas)), f"Reglas repetidas: {reglas}"
    assert set(reglas) == {"vigencia", "cobertura", "carencia", "monto", "documentos"}
    