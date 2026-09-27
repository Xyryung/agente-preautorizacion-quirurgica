"""Issue #8: paginacion, reintentos 429 e idempotencia (todo con dobles, sin red)."""
import pytest

import preauth.notion_repo as nr


class Fake429(Exception):
    status = 429

    def __init__(self, retry_after="1"):
        super().__init__("rate limited")
        self.response = type("R", (), {"headers": {"Retry-After": retry_after}})()


class Fake400(Exception):
    status = 400

    def __init__(self):
        super().__init__("bad")
        self.response = type("R", (), {"headers": {}})()


# los reintentos solo dependen de .status/.response, no de la clase real
nr._es_429 = lambda err: getattr(err, "status", None) == 429


def test_query_all_pagina_150_en_dos_paginas(monkeypatch):
    pag1 = {"results": [{"id": f"p{i}"} for i in range(100)], "has_more": True, "next_cursor": "c2"}
    pag2 = {"results": [{"id": f"p{i}"} for i in range(100, 150)], "has_more": False, "next_cursor": None}
    llamadas = []

    class DS:
        def query(self, **kw):
            llamadas.append(kw)
            return pag1 if kw.get("start_cursor") is None else pag2

    class FakeClient:
        data_sources = DS()

    monkeypatch.setattr(nr, "get_client", lambda: FakeClient())
    out = nr.query_all("ds", dormir=lambda s: None)
    assert len(out) == 150
    assert llamadas[1]["start_cursor"] == "c2"


def test_con_reintentos_respeta_retry_after():
    intentos = []
    esperas = []

    def flaky():
        intentos.append(1)
        if len(intentos) < 3:
            raise Fake429(retry_after="2")
        return "ok"

    assert nr.con_reintentos(flaky, dormir=esperas.append) == "ok"
    assert esperas == [2.0, 2.0]


def test_con_reintentos_no_reintenta_otros_errores():
    with pytest.raises(Fake400):
        nr.con_reintentos(lambda: (_ for _ in ()).throw(Fake400()), dormir=lambda s: None)


def test_doble_ejecucion_no_duplica(monkeypatch):
    creadas = []
    consultas_res = {"n": 0}

    class Pages:
        def create(self, **kw):
            creadas.append(kw)
            return {"id": "res1"}

        def update(self, **kw):
            return {}

    class DS:
        def query(self, **kw):
            f = kw.get("filter", {})
            if f.get("property") == "estado":
                return {"results": [{"id": "inf1", "properties": {}}], "has_more": False}
            consultas_res["n"] += 1  # filtro por relation informe
            if consultas_res["n"] == 1:
                return {"results": [], "has_more": False}
            return {"results": [{"id": "res1"}], "has_more": False}

    class FakeClient:
        data_sources = DS()
        pages = Pages()

    monkeypatch.setattr(nr, "get_client", lambda: FakeClient())
    monkeypatch.setattr(nr, "data_sources_ids",
                        lambda: {"informes": "a", "polizas": "b", "resoluciones": "c"})
    monkeypatch.setattr(nr, "fetch_poliza", lambda pid, **k: (object(), None))
    monkeypatch.setattr(nr, "evaluar", lambda pol, inf: {
        "decision": type("D", (), {"value": "PREAPROBADA"})(),
        "motivo": "ok", "faltantes": [], "autorizacion_id": "AUT-X"})
    monkeypatch.setattr(nr, "get_text", lambda pr, name: "P001" if "paciente" in name else "")
    monkeypatch.setattr(nr, "_autofill", lambda *a, **k: None)

    nr.run_once.__wrapped__ if hasattr(nr.run_once, "__wrapped__") else None
    nr.run_once(autofill=False)
    nr.run_once(autofill=False)
    assert len(creadas) == 1


def test_extraer_devuelve_confianza_y_citas():
    from preauth.extraccion import extraer_desde_texto
    ext = extraer_desde_texto(
        "Paciente P001 con colecistectomía programada, DNI y consentimiento.",
        proveedor="regex")
    assert ext["confianza"] == 0.0  # el regex no se autoevalua
    assert "analitica" in ext["citas_no_encontradas"]
    assert "identificacion" not in ext["citas_no_encontradas"]


def test_autofill_pasa_confianza_a_informe(monkeypatch):
    from preauth.reglas import InformeMedico
    vistos = {}

    class Pages:
        def create(self, **kw):
            return {"id": "res1"}

        def update(self, **kw):
            return {}

    class DS:
        def query(self, **kw):
            f = kw.get("filter", {})
            if f.get("property") == "estado":
                return {"results": [{"id": "inf1", "properties": {}}], "has_more": False}
            return {"results": [], "has_more": False}

    class FakeClient:
        data_sources = DS()
        pages = Pages()

    def fake_evaluar(pol, inf):
        vistos["conf"] = inf.confianza_extraccion
        vistos["citas"] = inf.citas_no_encontradas
        return {"decision": type("D", (), {"value": "PREAPROBADA"})(),
                "motivo": "ok", "faltantes": [], "autorizacion_id": "AUT-X"}

    monkeypatch.setattr(nr, "get_client", lambda: FakeClient())
    monkeypatch.setattr(nr, "data_sources_ids",
                        lambda: {"informes": "a", "polizas": "b", "resoluciones": "c"})
    monkeypatch.setattr(nr, "fetch_poliza", lambda pid, **k: (object(), "pol1"))
    monkeypatch.setattr(nr, "evaluar", fake_evaluar)
    monkeypatch.setattr(nr, "get_text", lambda pr, name: "P001" if "paciente" in name else "")
    monkeypatch.setattr(nr, "_autofill",
                        lambda *a, **k: {"confianza": 0.3, "citas_no_encontradas": ["analitica"]})

    nr.run_once(autofill=True)
    assert vistos["conf"] == 0.3
    assert vistos["citas"] == ["analitica"]
    # sin autofill, defaults del dataclass
    assert InformeMedico("P", "X", "K", "M").confianza_extraccion == 1.0


def test_autofill_evalua_con_los_datos_extraidos_del_texto(monkeypatch):
    """Un informe que llega solo con texto: la decision debe usar lo extraido
    (costo, medico, diagnostico), no solo lo que se guarda en Notion."""
    from datetime import date

    from preauth import esquema as E
    from preauth.reglas import Poliza

    guardado, creadas, evaluados = {}, [], []

    class Pages:
        def update(self, page_id, properties):
            guardado.update(properties)

        def create(self, **kw):
            creadas.append(kw)

    class FakeClient:
        pages = Pages()

    texto = ("Paciente P002 con hernia inguinal, diagnostico K40. Cirugia programada por la "
             "Dra. Lopez. Se adjuntan identificacion, informe medico, consentimiento informado, "
             "analitica y presupuesto del hospital por 6000.")
    pg = {"id": "inf1", "properties": {
        E.INF_PACIENTE_ID: {"title": [{"plain_text": "P002"}]},
        E.INF_TEXTO: {"rich_text": [{"plain_text": texto}]},
    }}
    poliza = Poliza("P002", ["Hernia inguinal"], date(2025, 1, 15), {"default": 6}, 80000, 0)
    evaluar_real = nr.evaluar
    monkeypatch.setattr(nr, "fetch_poliza", lambda pid, **k: (poliza, None))
    monkeypatch.setattr(nr, "resolucion_existe", lambda page_id: False)
    monkeypatch.setattr(nr, "evaluar", lambda pol, inf: evaluados.append(inf) or evaluar_real(pol, inf))

    nr.procesar_informe(FakeClient(), "res", pg)

    inf = evaluados[0]
    assert inf.costo_estimado == 6000
    assert inf.diagnostico_cie10 == "K40"
    assert inf.medico == "Dra. Lopez"
    assert guardado[E.INF_COSTO] == {"number": 6000}
    decision = creadas[0]["properties"][E.RES_DECISION]["select"]["name"]
    assert decision == "PREAPROBADA"


def test_get_text_lee_el_formato_de_lectura_y_el_de_escritura():
    leido = {"x": {"rich_text": [{"plain_text": "Dra. "}, {"plain_text": "Lopez"}]}}
    escrito = {"x": {"title": [{"text": {"content": "P002"}}]}}
    assert nr.get_text(leido, "x") == "Dra. Lopez"
    assert nr.get_text(escrito, "x") == "P002"


def test_confianza_cero_no_se_enmascara_y_manual_usa_defaults(monkeypatch):
    """#33: ext con 0.0 llega como 0.0; sin ext (manual) van los defaults."""
    vistos = []

    class Pages:
        def create(self, **kw):
            return {"id": "res1"}

        def update(self, **kw):
            return {}

    class DS:
        def query(self, **kw):
            f = kw.get("filter", {})
            if f.get("property") == "estado":
                return {"results": [{"id": "inf1", "properties": {}}], "has_more": False}
            return {"results": [], "has_more": False}

    class FakeClient:
        data_sources = DS()
        pages = Pages()

    def fake_evaluar(pol, inf):
        vistos.append((inf.confianza_extraccion, inf.citas_no_encontradas))
        return {"decision": type("D", (), {"value": "PREAPROBADA"})(),
                "motivo": "ok", "faltantes": [], "autorizacion_id": "AUT-X"}

    monkeypatch.setattr(nr, "get_client", lambda: FakeClient())
    monkeypatch.setattr(nr, "data_sources_ids",
                        lambda: {"informes": "a", "polizas": "b", "resoluciones": "c"})
    monkeypatch.setattr(nr, "fetch_poliza", lambda pid, **k: (object(), "pol1"))
    monkeypatch.setattr(nr, "evaluar", fake_evaluar)
    monkeypatch.setattr(nr, "get_text", lambda pr, name: "P001" if "paciente" in name else "")

    monkeypatch.setattr(nr, "_autofill",
                        lambda *a, **k: {"confianza": 0.0, "citas_no_encontradas": []})
    nr.run_once(autofill=True)
    assert vistos[-1] == (0.0, [])

    monkeypatch.setattr(nr, "_autofill", lambda *a, **k: None)
    nr.run_once(autofill=True)
    assert vistos[-1] == (1.0, [])
