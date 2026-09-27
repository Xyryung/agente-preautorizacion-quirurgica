"""Pruebas del issue #37: la IA y la busqueda por palabras clave difieren.

Si ambos procedimientos son conocidos y difieren, el caso va a
REVISION_MANUAL con los dos valores en el motivo (nunca denegacion
por esta regla). Si la regex no encuentra nada, no se marca.
"""
from datetime import date
from types import SimpleNamespace

from preauth.extraccion import DESCONOCIDO, ExtraccionInforme, extraer
from preauth.reglas import Decision, InformeMedico, Poliza, evaluar

S12 = ("Paciente P012. Catarata senil del ojo derecho (H25.1). Se programa "
       "artroplastia total de rodilla. Se presentan radiografía y analítica. "
       "Costo estimado 11000 USD.")


def salida_ia(**cambios):
    datos = dict(
        procedimiento="Cataratas", cie10="H25.1", urgencia="programada",
        costo_estimado=11000.0, paciente_id="P012", medico="Dra. Ríos",
        documentos_aportados=["analitica", "radiografia"], documentos_pendientes=[],
        coherencia_diagnostico="coherente",
        justificacion_coherencia="H25.1 justifica las cataratas.",
        evidencia=[], confianza=0.9,
    )
    datos.update(cambios)
    return ExtraccionInforme(**datos)


class ClienteFalso:
    def __init__(self, salida):
        self.responses = SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(output_parsed=salida))


def poliza_cataratas():
    return Poliza("P012", ["Cataratas", "Artroplastia"], date(2024, 1, 1),
                  {"default": 0}, 50000, 0)


def informe_cataratas(**cambios):
    base = dict(paciente_id="P012", procedimiento="Cataratas", diagnostico_cie10="H25.1",
                medico="Dra. Ríos", documentos_adjuntos=["identificacion", "informe_medico",
                "consentimiento", "presupuesto_hospital"], costo_estimado=11000)
    base.update(cambios)
    return InformeMedico(**base)


def test_extraer_marca_discrepancia_con_s12():
    r = extraer(S12, proveedor="openai", cliente=ClienteFalso(salida_ia()))
    assert r.datos.procedimiento == "Cataratas"
    assert r.procedimiento_regex == "Artroplastia"


def test_s12_va_a_revision_manual_con_ambos_valores():
    r = evaluar(poliza_cataratas(), informe_cataratas(procedimiento_regex="Artroplastia"))
    assert r["decision"] == Decision.REVISION_MANUAL
    assert "Cataratas" in r["motivo"] and "Artroplastia" in r["motivo"]
    reglas = {h.regla for h in r["hallazgos"] if h.resultado.value == "revision"}
    assert "procedimiento" in reglas


def test_discrepancia_nunca_deniega():
    # Lo unico raro es la discrepancia: todo lo demas cumple.
    r = evaluar(poliza_cataratas(), informe_cataratas(procedimiento_regex="Artroplastia"))
    assert r["decision"] != Decision.DENEGADA


def test_sin_procedimiento_en_regex_no_se_marca():
    r = extraer("Paciente P009. Se envía documentación.", proveedor="regex")
    assert r.procedimiento_regex is None
    inf = informe_cataratas(procedimiento_regex=None)
    r2 = evaluar(poliza_cataratas(), inf)
    assert all(h.regla != "procedimiento" for h in r2["hallazgos"])


def test_ia_desconocido_no_se_marca():
    r = extraer(S12, proveedor="openai",
                cliente=ClienteFalso(salida_ia(procedimiento=DESCONOCIDO)))
    assert r.procedimiento_regex is None
