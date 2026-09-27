"""Pruebas del issue #7: coherencia entre diagnostico CIE-10 y procedimiento.

La IA da un veredicto (coherente / incoherente / no_evaluable) con una justificacion;
la regla 'coherencia' de reglas.py envia lo incoherente a revision manual. Nunca deniega.
Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from datetime import date
from types import SimpleNamespace

import pydantic
import pytest

import preauth.notion_repo as nr
from preauth.extraccion import ExtraccionInforme, extraer, extraer_desde_texto
from preauth.reglas import Decision, InformeMedico, Poliza, Resultado, evaluar

HOY = date(2026, 9, 26)
DOCS_COMPLETOS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]
TEXTO = "Diagnóstico Z41.1. Se solicita colecistectomía programada."
JUSTIFICACION = "Z41.1 corresponde a un procedimiento estético y no justifica una colecistectomía."


def poliza() -> Poliza:
    return Poliza("P001", ["Colecistectomía"], date(2024, 1, 1), {"default": 8}, 50000, 5000)


def informe(**cambios) -> InformeMedico:
    datos = dict(paciente_id="P001", procedimiento="Colecistectomía", diagnostico_cie10="Z41.1",
                 medico="Dr. Gil", documentos_adjuntos=list(DOCS_COMPLETOS), costo_estimado=8000)
    datos.update(cambios)
    return InformeMedico(**datos)


def hallazgos_de(r, regla: str) -> list:
    return [h for h in r["hallazgos"] if h.regla == regla]


def extraccion(**cambios) -> ExtraccionInforme:
    datos = dict(
        procedimiento="Colecistectomía", cie10="Z41.1", urgencia="programada", costo_estimado=8000.0,
        paciente_id=None, medico=None, documentos_aportados=[], documentos_pendientes=[],
        coherencia_diagnostico="incoherente", justificacion_coherencia=JUSTIFICACION,
        evidencia=[{"campo": "cie10", "cita": "Diagnóstico Z41.1"}], confianza=0.9)
    datos.update(cambios)
    return ExtraccionInforme(**datos)


class ClienteFalso:
    def __init__(self, salida):
        self.responses = SimpleNamespace(parse=lambda **kw: SimpleNamespace(output_parsed=salida))


# --- Regla 'coherencia' en evaluar() ------------------------------------------

def test_incoherente_va_a_revision_manual_con_la_justificacion():
    r = evaluar(poliza(), informe(coherencia_diagnostico="incoherente",
                                  justificacion_coherencia=JUSTIFICACION), HOY)
    assert r["decision"] is Decision.REVISION_MANUAL
    [h] = hallazgos_de(r, "coherencia")
    assert JUSTIFICACION in h.mensaje
    assert JUSTIFICACION in r["motivo"]


@pytest.mark.parametrize("veredicto", ["coherente", "no_evaluable", None])
def test_solo_incoherente_genera_hallazgo(veredicto):
    r = evaluar(poliza(), informe(coherencia_diagnostico=veredicto), HOY)
    assert r["decision"] is Decision.PREAPROBADA
    assert hallazgos_de(r, "coherencia") == []


def test_la_coherencia_nunca_deniega():
    r = evaluar(poliza(), informe(coherencia_diagnostico="incoherente"), HOY)
    [h] = hallazgos_de(r, "coherencia")
    assert h.resultado is Resultado.REVISION
    assert r["decision"] is not Decision.DENEGADA


def test_una_denegacion_por_cobertura_sigue_ganando():
    r = evaluar(poliza(), informe(procedimiento="Rinoplastia estética",
                                  coherencia_diagnostico="incoherente"), HOY)
    assert r["decision"] is Decision.DENEGADA


def test_sin_justificacion_el_mensaje_sigue_siendo_legible():
    r = evaluar(poliza(), informe(coherencia_diagnostico="incoherente",
                                  justificacion_coherencia="  "), HOY)
    [h] = hallazgos_de(r, "coherencia")
    assert "sin justificación" in h.mensaje


# --- Extraccion ------------------------------------------------------------------

def test_el_esquema_solo_acepta_los_tres_veredictos():
    with pytest.raises(pydantic.ValidationError):
        extraccion(coherencia_diagnostico="dudoso")


def test_extraer_devuelve_el_veredicto_del_modelo():
    r = extraer(TEXTO, proveedor="openai", cliente=ClienteFalso(extraccion()))
    assert r.datos.coherencia_diagnostico == "incoherente"
    assert r.datos.justificacion_coherencia == JUSTIFICACION


def test_sin_ia_la_coherencia_no_se_evalua():
    r = extraer(TEXTO, proveedor="regex")
    assert r.datos.coherencia_diagnostico == "no_evaluable"


def test_compatibilidad_incluye_el_veredicto_y_la_justificacion():
    d = extraer_desde_texto(TEXTO, proveedor="openai", cliente=ClienteFalso(extraccion()))
    assert d["coherencia"] == "incoherente"
    assert d["justificacion_coherencia"] == JUSTIFICACION


# --- Paso por el autofill de notion_repo -----------------------------------------

def _ejecutar_con_autofill(monkeypatch, ext):
    vistos = {}

    class DS:
        def query(self, **kw):
            if kw.get("filter", {}).get("property") == "estado":
                return {"results": [{"id": "inf1", "properties": {}}], "has_more": False}
            return {"results": [], "has_more": False}

    class FakeClient:
        data_sources = DS()
        pages = SimpleNamespace(create=lambda **kw: {"id": "res1"}, update=lambda **kw: {})

    def fake_evaluar(pol, inf):
        vistos["inf"] = inf
        return {"decision": Decision.REVISION_MANUAL, "motivo": "revision", "faltantes": []}

    monkeypatch.setattr(nr, "get_client", lambda: FakeClient())
    monkeypatch.setattr(nr, "data_sources_ids",
                        lambda: {"informes": "a", "polizas": "b", "resoluciones": "c"})
    monkeypatch.setattr(nr, "fetch_poliza", lambda pid, **k: (poliza(), "pol1"))
    monkeypatch.setattr(nr, "evaluar", fake_evaluar)
    monkeypatch.setattr(nr, "get_text", lambda pr, name: "P001" if "paciente" in name else "")
    monkeypatch.setattr(nr, "_autofill", lambda *a, **k: ext)
    nr.run_once(autofill=True)
    return vistos["inf"]


def test_el_autofill_pasa_la_coherencia_al_informe(monkeypatch):
    inf = _ejecutar_con_autofill(monkeypatch, {
        "confianza": 0.9, "citas_no_encontradas": [],
        "coherencia": "incoherente", "justificacion_coherencia": JUSTIFICACION})
    assert inf.coherencia_diagnostico == "incoherente"
    assert inf.justificacion_coherencia == JUSTIFICACION


def test_sin_extraccion_la_coherencia_queda_vacia(monkeypatch):
    inf = _ejecutar_con_autofill(monkeypatch, None)
    assert inf.coherencia_diagnostico is None
    assert inf.justificacion_coherencia == ""
