"""Pruebas del servicio web con TestClient (sin red, sin Notion, sin OpenAI)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_responde_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_demo_devuelve_los_tres_casos():
    r = client.get("/api/demo")
    assert r.status_code == 200
    decisiones = [c["decision"] for c in r.json()]
    assert decisiones == ["PREAPROBADA", "SOLICITUD_DOCUMENTOS_FALTANTES", "DENEGADA"]


def test_api_demo_incluye_campos_esperados():
    for caso in client.get("/api/demo").json():
        assert set(caso) == {"caso", "procedimiento", "decision", "motivo", "faltantes", "latencia_ms"}
        assert caso["latencia_ms"] >= 0


def test_pagina_principal_muestra_el_formulario():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="formulario"' in r.text
    assert "/api/evaluar" in r.text


def test_pagina_tiene_boton_guardar_en_notion():
    r = client.get("/")
    assert 'id="guardar"' in r.text
    assert "/api/guardar-informe" in r.text


def test_guardar_sin_token_da_401(monkeypatch):
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "secreto")
    r = client.post("/api/guardar-informe", json={"texto": "Paciente P001."})
    assert r.status_code == 401


def test_guardar_sin_config_da_503(monkeypatch):
    monkeypatch.delenv("DEMO_ADMIN_TOKEN", raising=False)
    r = client.post("/api/guardar-informe", json={"texto": "Paciente P001."},
                    headers={"Authorization": "Bearer x"})
    assert r.status_code == 503


def test_guardar_crea_fila_pendiente(monkeypatch):
    import preauth.notion_repo as nr
    creadas = {}

    class Pages:
        def create(self, parent, properties):
            creadas["parent"] = parent
            creadas["properties"] = properties
            return {"id": "pag-nueva"}

    class FakeClient:
        pages = Pages()

    monkeypatch.setattr(nr, "get_client", lambda: FakeClient())
    monkeypatch.setattr(nr, "data_sources_ids", lambda: {"informes": "ds-inf"})
    monkeypatch.setattr(nr, "con_reintentos", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "secreto")

    r = client.post("/api/guardar-informe", json={"texto": "Paciente P001 con K80."},
                    headers={"Authorization": "Bearer secreto"})
    assert r.status_code == 200
    assert r.json() == {"status": "guardado", "page_id": "pag-nueva", "estado": "pendiente"}
    assert creadas["parent"] == {"data_source_id": "ds-inf"}
    props = creadas["properties"]
    assert props["informe_texto"] == {"rich_text": [{"text": {"content": "Paciente P001 con K80."}}]}
    assert props["estado"] == {"status": {"name": "pendiente"}}
