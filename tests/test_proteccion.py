"""Pruebas de las protecciones de la demo publica (issue #11)."""
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import proteccion
from app.main import app

client = TestClient(app)


def test_20_solicitudes_seguidas_misma_ip_da_429():
    codigos = [client.get("/api/demo").status_code for _ in range(20)]
    assert codigos[0] == 200
    assert codigos[-1] == 429
    r = client.get("/api/demo")
    assert r.headers["retry-after"] == "60"
    assert "Demasiadas solicitudes" in r.json()["detail"]


def test_ips_distintas_tienen_limites_separados():
    for _ in range(20):
        client.get("/api/demo", headers={"CF-Connecting-IP": "10.0.0.1"})
    assert client.get("/api/demo", headers={"CF-Connecting-IP": "10.0.0.2"}).status_code == 200


def test_true_client_ip_falso_no_salta_el_limite():
    for i in range(20):
        client.get("/api/demo", headers={"CF-Connecting-IP": "10.0.0.9", "True-Client-IP": f"1.1.1.{i}"})
    r = client.get("/api/demo", headers={"CF-Connecting-IP": "10.0.0.9", "True-Client-IP": "9.9.9.9"})
    assert r.status_code == 429


def test_health_no_cuenta_para_el_limite():
    assert all(client.get("/health").status_code == 200 for _ in range(30))


def test_navegar_la_pagina_y_docs_no_gasta_cupo_de_la_api():
    for ruta in ["/", "/docs", "/openapi.json", "/favicon.ico"] * 5:
        client.get(ruta)
    assert client.get("/api/demo").status_code == 200


def test_ventana_deslizante_libera_tras_un_minuto():
    lim = proteccion.LimitadorPorIP()
    assert all(lim.permitir("ip", 3, ahora=t) for t in (0, 1, 2))
    assert not lim.permitir("ip", 3, ahora=30)
    assert lim.permitir("ip", 3, ahora=61)


def test_texto_de_50000_caracteres_se_rechaza_con_mensaje_claro():
    r = client.post("/api/demo", json={"texto": "a" * 50_000})
    assert r.status_code == 413
    assert "caracteres" in r.json()["detail"]


def app_con_endpoint_admin() -> TestClient:
    mini = FastAPI()

    @mini.post("/escribir", dependencies=[Depends(proteccion.requiere_admin)])
    def escribir():
        return {"ok": True}

    return TestClient(mini)


def test_admin_sin_configurar_deshabilita_el_endpoint(monkeypatch):
    monkeypatch.delenv("DEMO_ADMIN_TOKEN", raising=False)
    assert app_con_endpoint_admin().post("/escribir").status_code == 503


def test_admin_rechaza_token_ausente_o_incorrecto(monkeypatch):
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "secreto-de-prueba")
    c = app_con_endpoint_admin()
    assert c.post("/escribir").status_code == 401
    assert c.post("/escribir", headers={"Authorization": "Bearer otro"}).status_code == 401


def test_admin_acepta_token_correcto(monkeypatch):
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "secreto-de-prueba")
    r = app_con_endpoint_admin().post("/escribir", headers={"Authorization": "Bearer secreto-de-prueba"})
    assert r.status_code == 200


def test_error_interno_no_expone_trazas_ni_secretos():
    mini = FastAPI()
    proteccion.instalar(mini)

    @mini.get("/falla")
    def falla():
        raise RuntimeError("OPENAI_API_KEY=sk-no-deberia-verse")

    r = TestClient(mini, raise_server_exceptions=False).get("/falla")
    assert r.status_code == 500
    assert r.json() == {"detail": "Error interno del servidor."}
    assert "sk-" not in r.text


def test_pagina_muestra_aviso_de_datos_sinteticos():
    assert "Solo datos sintéticos" in client.get("/").text
