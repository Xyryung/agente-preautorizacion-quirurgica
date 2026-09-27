"""Pruebas del webhook de Notion (issue #12). Sin red: el ciclo de Notion se simula."""
import hashlib
import hmac
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import webhook_notion
from app.main import app

TOKEN = "secret_token_de_prueba"
client = TestClient(app)


@pytest.fixture(autouse=True)
def estado_limpio(monkeypatch):
    webhook_notion.eventos_vistos.reiniciar()
    llamadas = []
    monkeypatch.setattr(webhook_notion.procesador, "ejecutar", lambda: llamadas.append(1))
    yield llamadas


def evento(evento_id="evt-1", tipo="page.created", entidad="page") -> bytes:
    # Notion firma el cuerpo tal cual lo envia (JSON minificado).
    return json.dumps({
        "id": evento_id, "timestamp": "2026-09-27T04:00:00.000Z", "type": tipo,
        "attempt_number": 1, "entity": {"id": "pagina-1", "type": entidad},
        "data": {"parent": {"id": "db-informes", "type": "database"}},
    }, separators=(",", ":")).encode()


def firmar(cuerpo: bytes, token: str = TOKEN) -> str:
    return "sha256=" + hmac.new(token.encode(), cuerpo, hashlib.sha256).hexdigest()


def enviar(cuerpo: bytes, firma: str | None = None):
    headers = {"Content-Type": "application/json"}
    if firma is not None:
        headers["X-Notion-Signature"] = firma
    return client.post("/webhook/notion", content=cuerpo, headers=headers)


# --- Handshake ---

def test_handshake_sin_token_configurado_se_acepta_y_se_registra(monkeypatch, caplog):
    monkeypatch.delenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", raising=False)
    r = enviar(b'{"verification_token":"secret_nuevo"}')
    assert r.status_code == 200
    assert "secret_nuevo" in caplog.text


def test_handshake_con_token_ya_configurado_se_ignora(monkeypatch, caplog):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    r = enviar(b'{"verification_token":"secret_de_un_atacante"}')
    assert r.json() == {"status": "ignorado"}
    assert "secret_de_un_atacante" not in caplog.text


# --- Firma ---

def test_firma_invalida_da_401(monkeypatch, estado_limpio):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    cuerpo = evento()
    assert enviar(cuerpo, firmar(cuerpo, "otro_token")).status_code == 401
    assert estado_limpio == []


def test_sin_firma_da_401(monkeypatch):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    assert enviar(evento()).status_code == 401


def test_cuerpo_alterado_tras_firmar_da_401(monkeypatch):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    firma = firmar(evento())
    assert enviar(evento(evento_id="evt-alterado"), firma).status_code == 401


def test_sin_token_configurado_los_eventos_dan_503(monkeypatch):
    monkeypatch.delenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", raising=False)
    cuerpo = evento()
    assert enviar(cuerpo, firmar(cuerpo)).status_code == 503


def test_json_invalido_da_400():
    assert enviar(b"no es json").status_code == 400


# --- Eventos ---

def test_evento_valido_responde_200_y_dispara_el_ciclo(monkeypatch, estado_limpio):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    cuerpo = evento()
    r = enviar(cuerpo, firmar(cuerpo))
    assert r.status_code == 200
    assert r.json() == {"status": "aceptado"}
    assert estado_limpio == [1]


def test_evento_repetido_se_procesa_una_sola_vez(monkeypatch, estado_limpio):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    cuerpo = evento("evt-reintento")
    assert enviar(cuerpo, firmar(cuerpo)).json() == {"status": "aceptado"}
    assert enviar(cuerpo, firmar(cuerpo)).json() == {"status": "duplicado"}
    assert estado_limpio == [1]


@pytest.mark.parametrize("tipo,entidad", [
    ("comment.created", "comment"),
    ("data_source.schema_updated", "data_source"),
    ("page.deleted", "page"),
])
def test_eventos_irrelevantes_no_disparan_el_ciclo(monkeypatch, estado_limpio, tipo, entidad):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    cuerpo = evento(f"evt-{tipo}", tipo, entidad)
    assert enviar(cuerpo, firmar(cuerpo)).json() == {"status": "ignorado"}
    assert estado_limpio == []


def test_el_webhook_no_gasta_el_cupo_de_la_api(monkeypatch):
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", TOKEN)
    for i in range(15):
        cuerpo = evento(f"evt-{i}")
        assert enviar(cuerpo, firmar(cuerpo)).status_code == 200


# --- Procesador: nunca dos ciclos a la vez ---

def test_procesador_no_corre_en_paralelo_y_repite_una_vez():
    p = webhook_notion.Procesador()
    dentro, soltar = threading.Event(), threading.Event()
    ejecuciones = []

    def ciclo_lento():
        ejecuciones.append(1)
        if len(ejecuciones) == 1:
            dentro.set()
            soltar.wait(5)

    hilo = threading.Thread(target=p.ejecutar, args=(ciclo_lento,))
    hilo.start()
    dentro.wait(5)
    p.ejecutar(ciclo_lento)  # llega mientras corre: se marca, no corre en paralelo
    p.ejecutar(ciclo_lento)  # varios eventos se agrupan en una sola repeticion
    assert ejecuciones == [1]
    soltar.set()
    hilo.join(5)
    assert ejecuciones == [1, 1]


def test_procesador_sigue_funcionando_si_el_ciclo_falla():
    p = webhook_notion.Procesador()
    llamadas = []

    def ciclo_que_falla():
        llamadas.append(1)
        raise RuntimeError("Notion no responde")

    p.ejecutar(ciclo_que_falla)
    p.ejecutar(ciclo_que_falla)
    assert llamadas == [1, 1]
