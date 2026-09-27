"""Pruebas del issue #6b: senales de la extraccion automatica -> REVISION_MANUAL.

La IA no decide: aporta confianza y citas; la regla 'extraccion' de reglas.py actua.
Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from datetime import date
from types import SimpleNamespace

from preauth.extraccion import ExtraccionInforme, citas_no_encontradas, extraer
from preauth.reglas import (
    UMBRAL_CONFIANZA, Decision, InformeMedico, Poliza, Resultado, evaluar,
)

HOY = date(2026, 9, 26)
DOCS_COMPLETOS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]
TEXTO = "Se indica colecistectomía laparoscópica programada. Presupuesto 8000 USD."


def poliza() -> Poliza:
    return Poliza("P001", ["Colecistectomía"], date(2024, 1, 1), {"default": 8}, 50000, 5000)


def informe(**cambios) -> InformeMedico:
    datos = dict(paciente_id="P001", procedimiento="Colecistectomía", diagnostico_cie10="K80",
                 medico="Dr. Gil", documentos_adjuntos=list(DOCS_COMPLETOS), costo_estimado=8000)
    datos.update(cambios)
    return InformeMedico(**datos)


def hallazgos_de(r, regla: str) -> list:
    return [h for h in r["hallazgos"] if h.regla == regla]


def extraccion(evidencia) -> ExtraccionInforme:
    return ExtraccionInforme(
        procedimiento="Colecistectomía", cie10=None, urgencia="programada", costo_estimado=8000.0,
        paciente_id=None, medico=None, documentos_aportados=[], documentos_pendientes=[],
        evidencia=evidencia, confianza=0.9)


# --- Regla 'extraccion' en evaluar() -----------------------------------------

def test_sin_extraccion_automatica_no_hay_hallazgo():
    r = evaluar(poliza(), informe(), HOY)
    assert r["decision"] is Decision.PREAPROBADA
    assert hallazgos_de(r, "extraccion") == []


def test_confianza_alta_no_cambia_la_decision():
    r = evaluar(poliza(), informe(confianza_extraccion=0.9), HOY)
    assert r["decision"] is Decision.PREAPROBADA


def test_confianza_baja_va_a_revision_manual():
    r = evaluar(poliza(), informe(confianza_extraccion=0.6), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL
    [h] = hallazgos_de(r, "extraccion")
    assert h.resultado is Resultado.REVISION
    assert "0.60" in r["motivo"]


def test_confianza_igual_al_umbral_no_se_marca():
    r = evaluar(poliza(), informe(confianza_extraccion=UMBRAL_CONFIANZA), HOY)
    assert r["decision"] is Decision.PREAPROBADA


def test_respaldo_sin_ia_con_confianza_cero_va_a_revision_manual():
    r = evaluar(poliza(), informe(confianza_extraccion=0.0), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL


def test_cita_que_no_esta_en_el_informe_va_a_revision_manual():
    r = evaluar(poliza(), informe(confianza_extraccion=0.9,
                                  citas_no_encontradas=["costo de 9000 USD"]), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL
    assert "costo de 9000 USD" in r["motivo"]


def test_ambas_senales_producen_un_solo_hallazgo():
    r = evaluar(poliza(), informe(confianza_extraccion=0.5,
                                  citas_no_encontradas=["texto inventado"]), HOY)
    [h] = hallazgos_de(r, "extraccion")
    assert "0.50" in h.mensaje and "texto inventado" in h.mensaje


def test_una_denegacion_sigue_ganando_a_la_revision():
    r = evaluar(poliza(), informe(procedimiento="Rinoplastia estética",
                                  confianza_extraccion=0.5), HOY)
    assert r["decision"] is Decision.DENEGADA


# --- citas_no_encontradas() ----------------------------------------------------

def test_citas_textuales_se_encuentran_aunque_cambien_tildes_y_comillas():
    datos = extraccion([{"campo": "procedimiento", "cita": '"Colecistectomia laparoscopica programada."'}])
    assert citas_no_encontradas(datos, TEXTO) == []


def test_citas_inventadas_se_reportan():
    datos = extraccion([{"campo": "costo_estimado", "cita": "costo de 9000 USD"},
                        {"campo": "procedimiento", "cita": "colecistectomía laparoscópica"}])
    assert citas_no_encontradas(datos, TEXTO) == ["costo de 9000 USD"]


# --- extraer() y compatibilidad -------------------------------------------------

class ClienteFalso:
    def __init__(self, salida):
        self.responses = SimpleNamespace(parse=lambda **kw: SimpleNamespace(output_parsed=salida))


def test_extraer_informa_las_citas_no_encontradas():
    salida = extraccion([{"campo": "costo_estimado", "cita": "costo de 9000 USD"}])
    r = extraer(TEXTO, proveedor="openai", cliente=ClienteFalso(salida))
    assert r.citas_no_encontradas == ("costo de 9000 USD",)


def test_el_respaldo_regex_no_tiene_citas_que_verificar():
    assert extraer(TEXTO, proveedor="regex").citas_no_encontradas == ()
