"""Pruebas del issue #3: comparar texto sin depender de tildes, mayusculas ni espacios.

Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from datetime import date

import pytest

from preauth.extraccion import extraer_desde_texto
from preauth.reglas import Decision, InformeMedico, Poliza, evaluar
from preauth.texto import norm

HOY = date(2026, 9, 26)  # con fecha_inicio 2024-01-01 da 32 meses de afiliacion

# Sin presupuesto_hospital a proposito: la colecistectomia no lo exige, pero el
# caso "default" si. Asi las pruebas tambien verifican que se use la lista de
# documentos correcta para el procedimiento.
DOCS_COLECISTECTOMIA = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica",
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
        documentos_adjuntos=list(DOCS_COLECISTECTOMIA),
        costo_estimado=8000,
    )
    datos.update(cambios)
    return InformeMedico(**datos)


# --- norm() -----------------------------------------------------------------

@pytest.mark.parametrize("entrada, esperado", [
    ("Colecistectomía", "colecistectomia"),
    ("COLECISTECTOMÍA", "colecistectomia"),
    ("  colecistectomía  ", "colecistectomia"),
    ("Hernia   inguinal", "hernia inguinal"),
    ("Hernia\tinguinal\n", "hernia inguinal"),
    ("Cédula", "cedula"),
    ("", ""),
    (None, ""),
])
def test_norm(entrada, esperado):
    assert norm(entrada) == esperado


# --- evaluar() --------------------------------------------------------------

@pytest.mark.parametrize("procedimiento", [
    "Colecistectomía",
    "Colecistectomia",
    "COLECISTECTOMÍA",
    "  colecistectomía  ",
])
def test_variantes_del_procedimiento_se_preaprueban(procedimiento):
    r = evaluar(poliza(), informe(procedimiento), HOY)
    assert r["decision"] is Decision.PREAPROBADA


def test_exclusion_escrita_distinto_igual_se_aplica():
    p = poliza(cobertura_procedimientos=["Rinoplastia estética"],
               exclusiones=["RINOPLASTIA ESTETICA"])
    r = evaluar(p, informe("Rinoplastia estética"), HOY)
    assert r["decision"] is Decision.DENEGADA
    assert "exclusiones" in r["motivo"]


def test_carencia_por_procedimiento_escrita_distinto_igual_se_aplica():
    p = poliza(carencia_meses={"Colecistectomía": 40, "default": 0})
    r = evaluar(p, informe("colecistectomia"), HOY)
    assert r["decision"] is Decision.DENEGADA
    assert "32/40" in r["motivo"]


def test_emergencia_con_mayuscula_omite_la_carencia():
    p = poliza(carencia_meses={"default": 40})
    r = evaluar(p, informe(urgencia="Emergencia"), HOY)
    assert r["decision"] is Decision.PREAPROBADA


def test_documentos_escritos_distinto_cuentan_como_adjuntos():
    docs = ["Identificacion", "INFORME_MEDICO", " consentimiento ",
            "ecografía_abdominal", "Analítica"]
    r = evaluar(poliza(), informe(documentos_adjuntos=docs), HOY)
    assert r["decision"] is Decision.PREAPROBADA


def test_faltantes_se_informan_con_su_codigo_canonico():
    r = evaluar(poliza(), informe(documentos_adjuntos=["Identificacion"]), HOY)
    assert r["decision"] is Decision.DOCUMENTOS_FALTANTES
    # Comparar como conjunto: el orden de la lista se corrige en el issue #5.
    assert set(r["faltantes"]) == {
        "informe_medico", "consentimiento", "ecografia_abdominal", "analitica",
    }


def test_el_mensaje_muestra_el_nombre_original_no_el_normalizado():
    r = evaluar(poliza(), informe("Rinoplastia estética"), HOY)
    assert r["decision"] is Decision.DENEGADA
    assert "Rinoplastia estética" in r["motivo"]


# --- extraer_desde_texto() -------------------------------------------------

def test_extraccion_detecta_documentos_escritos_con_tilde():
    ext = extraer_desde_texto(
        "Se adjuntan cédula, ecografía abdominal, radiografía y segunda opinión."
    )
    assert {"identificacion", "ecografia_abdominal", "radiografia",
            "segunda_opinion"} <= set(ext["documentos"])


def test_extraccion_devuelve_el_procedimiento_canonico():
    ext = extraer_desde_texto("Paciente programado para COLECISTECTOMIA laparoscopica.")
    assert ext["procedimiento"] == "Colecistectomía"
