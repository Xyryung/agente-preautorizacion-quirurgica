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
