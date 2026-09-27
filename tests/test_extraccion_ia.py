"""Pruebas del issue #6a: extractor con salida estructurada y respaldo con regex.

Ninguna prueba llama a OpenAI: el proveedor openai recibe un cliente falso.
Ejecutar desde la raiz del repo:  python -m pytest -v
"""
from types import SimpleNamespace

import pydantic
import pytest

from preauth.config import ConfigError, llm_settings
from preauth.extraccion import (
    MAX_CARACTERES, ExtraccionInforme, extraer, extraer_desde_texto,
)

TEXTO = "Paciente P001. Colecistectomía programada. Se adjunta analítica."


def extraccion(**cambios) -> ExtraccionInforme:
    datos = dict(
        procedimiento="Colecistectomía", cie10="K80.2", urgencia="programada",
        costo_estimado=8000.0, paciente_id="P001", medico="Dra. Ríos",
        documentos_aportados=["analitica"], documentos_pendientes=["ecografia_abdominal"],
        coherencia_diagnostico="coherente", justificacion_coherencia="K80.2 justifica la colecistectomía.",
        evidencia=[{"campo": "procedimiento", "cita": "Colecistectomía programada"}],
        confianza=0.9,
    )
    datos.update(cambios)
    return ExtraccionInforme(**datos)


class ClienteFalso:
    """Imita cliente.responses.parse(): devuelve 'salida' o lanza 'error'."""

    def __init__(self, salida=None, error=None):
        self.llamadas = []
        self._salida, self._error = salida, error
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(output_parsed=self._salida)


# --- El esquema ---------------------------------------------------------------

def test_el_esquema_rechaza_valores_fuera_de_las_listas():
    with pytest.raises(pydantic.ValidationError):
        extraccion(procedimiento="Colecistectomía laparoscópica")
    with pytest.raises(pydantic.ValidationError):
        extraccion(documentos_aportados=["ecografia"])


# --- Proveedor openai (cliente falso) ----------------------------------------

def test_openai_devuelve_la_extraccion_del_modelo():
    cliente = ClienteFalso(salida=extraccion())
    r = extraer(TEXTO, proveedor="openai", cliente=cliente)
    assert r.proveedor == "openai"
    assert r.error is None
    assert r.datos.procedimiento == "Colecistectomía"
    assert r.latencia_ms >= 0


def test_se_envia_el_esquema_el_modelo_y_no_se_almacena():
    cliente = ClienteFalso(salida=extraccion())
    extraer(TEXTO, proveedor="openai", cliente=cliente)
    llamada = cliente.llamadas[0]
    assert llamada["text_format"] is ExtraccionInforme
    assert llamada["model"] == llm_settings().openai_model
    assert llamada["store"] is False
    assert llamada["reasoning"] == {"effort": llm_settings().openai_reasoning_effort}
    assert TEXTO in str(llamada["input"])


def test_si_openai_falla_se_usa_regex_y_se_registra_el_error():
    cliente = ClienteFalso(error=TimeoutError("sin respuesta en 20 s"))
    r = extraer(TEXTO, proveedor="openai", cliente=cliente)
    assert r.proveedor == "regex"
    assert "TimeoutError" in r.error
    assert r.datos.procedimiento == "Colecistectomía"


def test_si_el_modelo_se_niega_se_usa_regex():
    r = extraer(TEXTO, proveedor="openai", cliente=ClienteFalso(salida=None))
    assert r.proveedor == "regex"
    assert r.error


def test_confianza_fuera_de_rango_se_acota():
    r = extraer(TEXTO, proveedor="openai", cliente=ClienteFalso(salida=extraccion(confianza=1.7)))
    assert r.datos.confianza == 1.0


def test_listas_de_documentos_sin_repetidos_y_ordenadas():
    salida = extraccion(documentos_aportados=["consentimiento", "analitica", "analitica"])
    r = extraer(TEXTO, proveedor="openai", cliente=ClienteFalso(salida=salida))
    assert r.datos.documentos_aportados == ["analitica", "consentimiento"]


# --- Proveedor regex ------------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "Colecistectomía. El caso no es urgente.",
    "Colecistectomía urgente.",
    "Colecistectomía programada.",
])
def test_regex_nunca_infiere_la_urgencia_desde_el_texto(texto):
    r = extraer(texto, proveedor="regex")
    assert r.datos.urgencia == "no_indicada"


def test_regex_sin_procedimiento_reconocible_devuelve_desconocido():
    r = extraer("Paciente P009. Se envía documentación.", proveedor="regex")
    assert r.datos.procedimiento == "desconocido"
    assert r.datos.confianza == 0.0


def test_el_proveedor_por_defecto_en_pruebas_es_regex():
    # Garantiza que la suite nunca llama a OpenAI por accidente (ver conftest.py).
    assert extraer(TEXTO).proveedor == "regex"

def test_esfuerzo_de_razonamiento_por_defecto_es_minimal(monkeypatch):
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    llm_settings.cache_clear()
    assert llm_settings().openai_reasoning_effort == "minimal"


def test_esfuerzo_de_razonamiento_invalido_se_rechaza(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "turbo")
    llm_settings.cache_clear()
    with pytest.raises(ConfigError):
        llm_settings()

def test_proveedor_invalido_se_rechaza():
    with pytest.raises(ValueError):
        extraer(TEXTO, proveedor="gemini")


def test_texto_demasiado_largo_se_recorta_con_advertencia():
    cliente = ClienteFalso(salida=extraccion())
    r = extraer("x" * (MAX_CARACTERES + 500), proveedor="openai", cliente=cliente)
    assert len(cliente.llamadas[0]["input"][-1]["content"]) == MAX_CARACTERES
    assert any("recort" in a for a in r.advertencias)


# --- Compatibilidad con notion_repo (autofill) --------------------------------

def test_compatibilidad_mantiene_las_claves_del_diccionario():
    d = extraer_desde_texto(TEXTO)
    # Claves de siempre + #6b + #7 + discrepancia IA/regex.
    assert set(d) == {"procedimiento", "cie", "documentos", "urgencia",
                      "paciente_id", "medico", "costo",
                      "confianza", "citas_no_encontradas",
                      "coherencia", "justificacion_coherencia",
                      "procedimiento_regex"}


def test_compatibilidad_traduce_la_salida_del_modelo():
    salida = extraccion(urgencia="emergencia", cie10=None, costo_estimado=None)
    d = extraer_desde_texto(TEXTO, proveedor="openai", cliente=ClienteFalso(salida=salida))
    assert d["procedimiento"] == "Colecistectomía"
    assert d["urgencia"] == "emergencia"
    assert d["documentos"] == ["analitica"]      # solo aportados, no pendientes
    assert d["cie"] == "" and d["costo"] is None


def test_compatibilidad_deja_vacios_los_valores_sin_dato():
    # Vacio = autofill no escribe nada; las reglas usan "programada" y revision manual.
    d = extraer_desde_texto("Paciente P009. Se envía documentación.")
    assert d["procedimiento"] == ""
    assert d["urgencia"] == ""

def test_citas_no_encontradas_no_son_documentos_faltantes():
    # Todas las citas estan en el texto; faltan documentos requeridos.
    # 'citas_no_encontradas' debe quedar vacio: los faltantes los calcula reglas.py.
    salida = extraccion(evidencia=[{"campo": "procedimiento", "cita": "Colecistectomía programada"}],
                        documentos_aportados=["analitica"])
    d = extraer_desde_texto(TEXTO, proveedor="openai", cliente=ClienteFalso(salida=salida))
    assert d["citas_no_encontradas"] == []
    