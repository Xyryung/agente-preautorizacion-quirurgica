"""Pruebas de POST /api/evaluar, POST /api/procesar-pendientes y la pagina (issue #9).

Sin red: conftest.py fuerza LLM_PROVIDER=regex y el camino con IA se simula
sustituyendo el extractor con app.dependency_overrides.
"""
import pytest
from fastapi.testclient import TestClient

from app import api_evaluar, webhook_notion
from app.casos_demo import CASOS, POLIZAS_DEMO
from app.main import app
from preauth.extraccion import ExtraccionInforme, ResultadoExtraccion

client = TestClient(app)

TEXTO_S01 = next(c["texto"] for c in CASOS if c["id"] == "S01")


@pytest.fixture(autouse=True)
def sin_overrides():
    yield
    app.dependency_overrides.clear()


def datos_ia(**cambios) -> ExtraccionInforme:
    base = dict(procedimiento="Colecistectomía", cie10="K80.2", urgencia="programada", costo_estimado=8000,
                paciente_id="P001", medico="Dra. Ana Ríos",
                documentos_aportados=["identificacion", "informe_medico", "consentimiento",
                                      "ecografia_abdominal", "analitica", "presupuesto_hospital"],
                documentos_pendientes=[], evidencia=[], confianza=0.93)
    return ExtraccionInforme(**{**base, **cambios})


def usar_extractor(resultado: ResultadoExtraccion):
    llamadas = []
    app.dependency_overrides[api_evaluar.obtener_extractor] = lambda: (
        lambda texto: llamadas.append(texto) or resultado)
    return llamadas


# --- Texto libre ---

def test_texto_libre_con_ia_preaprueba_y_muestra_lo_extraido():
    llamadas = usar_extractor(ResultadoExtraccion(datos_ia(), "openai", 2900.0))
    r = client.post("/api/evaluar", json={"poliza_id": "P001", "texto": TEXTO_S01})
    assert r.status_code == 200
    cuerpo = r.json()
    assert llamadas == [TEXTO_S01]
    assert cuerpo["decision"] == "PREAPROBADA"
    assert cuerpo["autorizacion_id"].startswith("AUT-P001-")
    assert cuerpo["extraccion"]["proveedor"] == "openai"
    assert cuerpo["extraccion"]["respaldo"] is False
    assert cuerpo["extraccion"]["confianza"] == 0.93
    assert cuerpo["extraccion"]["datos"]["cie10"] == "K80.2"
    assert {h["regla"] for h in cuerpo["hallazgos"]} >= {"cobertura", "carencia", "monto", "documentos"}
    assert set(cuerpo["latencia"]) == {"extraccion_ms", "reglas_ms", "total_ms"}


def test_respaldo_de_la_ia_no_expone_el_error_interno():
    error = "AuthenticationError: Incorrect API key provided: sk-proj-secreto"
    usar_extractor(ResultadoExtraccion(datos_ia(confianza=0.0), "regex", 3.0, error))
    r = client.post("/api/evaluar", json={"poliza_id": "P001", "texto": TEXTO_S01})
    assert r.status_code == 200
    assert r.json()["extraccion"]["respaldo"] is True
    assert "sk-" not in r.text and "AuthenticationError" not in r.text


def test_texto_libre_con_regex_real_preaprueba_el_caso_s01():
    r = client.post("/api/evaluar", json={"poliza_id": "P001", "texto": TEXTO_S01})
    assert r.status_code == 200
    assert r.json()["decision"] == "PREAPROBADA"
    assert r.json()["extraccion"]["proveedor"] == "regex"


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_los_10_casos_sinteticos_se_evaluan_con_su_poliza_sugerida(caso):
    r = client.post("/api/evaluar", json={"poliza_id": caso["poliza_id"], "texto": caso["texto"]})
    assert r.status_code == 200
    assert r.json()["decision"] in {"PREAPROBADA", "SOLICITUD_DOCUMENTOS_FALTANTES", "REVISION_MANUAL", "DENEGADA"}


# --- Contrato de #6: extraccion -> InformeMedico ---

def test_conversion_de_la_extraccion_sigue_el_contrato():
    inf = api_evaluar.informe_desde_extraccion(
        datos_ia(urgencia="no_indicada", costo_estimado=None, cie10=None, medico=None,
                 procedimiento="desconocido", documentos_aportados=["identificacion"]), "P009")
    assert inf.paciente_id == "P009"
    assert inf.urgencia == "programada"
    assert inf.costo_estimado == 0
    assert inf.diagnostico_cie10 == "" and inf.medico == ""
    assert inf.procedimiento == "desconocido"
    assert inf.documentos_adjuntos == ["identificacion"]


def test_procedimiento_desconocido_va_a_revision_manual():
    usar_extractor(ResultadoExtraccion(datos_ia(procedimiento="desconocido"), "openai", 1.0))
    r = client.post("/api/evaluar", json={"poliza_id": "P001", "texto": "Paciente P008, bypass gastrico."})
    assert r.json()["decision"] == "REVISION_MANUAL"


# --- Informe estructurado y poliza propia ---

def test_informe_estructurado_con_poliza_propia():
    r = client.post("/api/evaluar", json={
        "poliza": {"paciente_id": "X1", "cobertura": ["Colecistectomía"], "fecha_inicio": "2024-01-01",
                   "carencia_meses": {"default": 8}, "monto_maximo": 50000},
        "informe": {"procedimiento": "Colecistectomía", "costo_estimado": 8000,
                    "documentos_adjuntos": ["identificacion", "informe_medico"]},
    })
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["decision"] == "SOLICITUD_DOCUMENTOS_FALTANTES"
    assert cuerpo["faltantes"] == ["analitica", "consentimiento", "ecografia_abdominal"]
    assert cuerpo["extraccion"] is None
    assert cuerpo["poliza_id"] == "X1"


def test_poliza_p003_no_cumple_carencia_salvo_emergencia():
    informe = {"procedimiento": "Apendicectomía", "costo_estimado": 3000,
               "documentos_adjuntos": ["identificacion", "informe_medico", "consentimiento", "presupuesto_hospital"]}
    programada = client.post("/api/evaluar", json={"poliza_id": "P003", "informe": informe}).json()
    emergencia = client.post("/api/evaluar", json={"poliza_id": "P003",
                                                   "informe": {**informe, "urgencia": "emergencia"}}).json()
    assert programada["decision"] == "DENEGADA"
    assert emergencia["decision"] == "PREAPROBADA"


def test_poliza_p004_excede_el_monto():
    r = client.post("/api/evaluar", json={"poliza_id": "p004", "informe": {
        "procedimiento": "Hernia inguinal", "costo_estimado": 2500,
        "documentos_adjuntos": ["identificacion", "informe_medico", "consentimiento", "presupuesto_hospital"]}})
    assert r.json()["decision"] == "DENEGADA"


# --- Validacion y protecciones de #11 ---

@pytest.mark.parametrize("cuerpo", [
    {"texto": "hola"},                                                 # sin poliza
    {"poliza_id": "P001"},                                             # sin informe
    {"poliza_id": "P001", "texto": "hola", "informe": {"procedimiento": "X"}},  # dos informes
    {"poliza_id": "P001", "texto": ""},                                # texto vacio
])
def test_solicitudes_mal_formadas_dan_422(cuerpo):
    assert client.post("/api/evaluar", json=cuerpo).status_code == 422


def test_poliza_inexistente_da_404():
    r = client.post("/api/evaluar", json={"poliza_id": "P999", "texto": "hola"})
    assert r.status_code == 404
    assert "P001" in r.json()["detail"]


def test_texto_de_mas_de_8000_caracteres_se_rechaza():
    assert client.post("/api/evaluar", json={"poliza_id": "P001", "texto": "a" * 8001}).status_code == 422
    assert client.post("/api/evaluar", json={"poliza_id": "P001", "texto": "a" * 50_000}).status_code == 413


def test_evaluar_respeta_el_limite_por_ip():
    usar_extractor(ResultadoExtraccion(datos_ia(), "openai", 1.0))
    codigos = [client.post("/api/evaluar", json={"poliza_id": "P001", "texto": "x"}).status_code
               for _ in range(11)]
    assert codigos[:10] == [200] * 10
    assert codigos[10] == 429


# --- Procesar pendientes de Notion ---

def test_procesar_pendientes_requiere_token(monkeypatch):
    monkeypatch.delenv("DEMO_ADMIN_TOKEN", raising=False)
    assert client.post("/api/procesar-pendientes").status_code == 503
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "token-de-prueba")
    assert client.post("/api/procesar-pendientes", headers={"Authorization": "Bearer otro"}).status_code == 401


def test_procesar_pendientes_con_token_lanza_el_ciclo(monkeypatch):
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "token-de-prueba")
    llamadas = []
    monkeypatch.setattr(webhook_notion.procesador, "ejecutar", lambda: llamadas.append(1))
    r = client.post("/api/procesar-pendientes", headers={"Authorization": "Bearer token-de-prueba"})
    assert r.status_code == 202
    assert llamadas == [1]


# --- Pagina ---

def test_pagina_incluye_casos_y_polizas_sin_gastar_cupo_de_la_api():
    r = client.get("/")
    assert r.status_code == 200
    assert "__DATOS__" not in r.text
    assert "S01" in r.text and all(pid in r.text for pid in POLIZAS_DEMO)
    assert "Solo datos sintéticos" in r.text
    # Cargar la pagina muchas veces no consume el limite de /api/*
    for _ in range(15):
        client.get("/")
    assert client.post("/api/evaluar", json={"poliza_id": "P001", "texto": TEXTO_S01}).status_code == 200


def test_openapi_documenta_los_endpoints_nuevos():
    rutas = client.get("/openapi.json").json()["paths"]
    assert "/api/evaluar" in rutas and "/api/procesar-pendientes" in rutas


def test_pagina_trae_los_campos_para_personalizar_la_poliza():
    import json, re
    datos = json.loads(re.search(r'<script id="datos" type="application/json">(.*?)</script>',
                                 client.get("/").text, re.S).group(1))
    p002 = datos["polizas"]["P002"]["campos"]
    assert p002["cobertura"] == ["Artroplastia", "Cataratas", "Hernia inguinal"]
    assert p002["carencia_meses"] == {"Artroplastia": 12, "default": 6}
    assert p002["requiere_segunda_opinion"] == ["Artroplastia"]
    # Los campos se pueden enviar tal cual como poliza personalizada
    r = client.post("/api/evaluar", json={"poliza": p002, "informe": {"procedimiento": "Cataratas", "costo_estimado": 3000}})
    assert r.status_code == 200
