"""Sincronizacion Notion <-> agente de pre-autorizacion.

Ejecutar desde la raiz del repo:  python -m preauth.notion_repo
"""
import json
import time
from datetime import date
from functools import lru_cache

from notion_client import Client
from notion_client.errors import APIResponseError

from preauth.config import notion_settings
from preauth import esquema as E
from preauth.extraccion import extraer_desde_texto
from preauth.reglas import InformeMedico, Poliza, evaluar


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Crea el cliente de Notion la primera vez que se necesita, no al importar."""
    return Client(auth=notion_settings().token)


def _resolver_data_source(notion: Client, db_id: str) -> str:
    """Resuelve un database_id a su data_source_id. Solo captura APIResponseError."""
    try:
        notion.data_sources.retrieve(data_source_id=db_id)
        return db_id  # ya era un data_source_id
    except APIResponseError:
        info = notion.databases.retrieve(database_id=db_id)
        ds = info.get("data_sources", [])
        if not ds:
            raise
        return ds[0]["id"]


@lru_cache(maxsize=1)
def data_sources_ids() -> dict:
    """Resuelve los tres data_source_id una sola vez al arrancar."""
    notion = get_client()
    cfg = notion_settings()
    return {
        "informes": _resolver_data_source(notion, cfg.db_informes),
        "polizas": _resolver_data_source(notion, cfg.db_polizas),
        "resoluciones": _resolver_data_source(notion, cfg.db_resoluciones),
    }


def _es_429(err: Exception) -> bool:
    return isinstance(err, APIResponseError) and getattr(err, "status", None) == 429


def _retry_after(err: Exception, intento: int) -> float:
    try:
        headers = getattr(getattr(err, "response", None), "headers", {}) or {}
        return float(headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return min(2 ** intento, 30)


def con_reintentos(fn, *args, intentos=4, dormir=time.sleep, **kw):
    """Reintenta con backoff ante HTTP 429 respetando Retry-After."""
    for i in range(intentos):
        try:
            return fn(*args, **kw)
        except Exception as err:
            if not _es_429(err) or i == intentos - 1:
                raise
            dormir(_retry_after(err, i))
    raise AssertionError("inaccesible")


def query_all(ds_id: str, dormir=time.sleep, **kw) -> list:
    """Consulta paginando con has_more/next_cursor hasta agotar resultados."""
    notion = get_client()
    resultados, cursor = [], None
    while True:
        params = dict(kw)
        if cursor:
            params["start_cursor"] = cursor
        resp = con_reintentos(
            notion.data_sources.query, data_source_id=ds_id, dormir=dormir, **params)
        resultados.extend(resp.get("results", []))
        if not resp.get("has_more"):
            return resultados
        cursor = resp.get("next_cursor")


def _texto_plano(t: dict) -> str:
    # Notion devuelve "plain_text"; lo que escribimos (autofill) usa text.content.
    return t.get("plain_text") or t.get("text", {}).get("content", "")


def get_text(props, name):
    p = props.get(name, {})
    if p.get("title"): return "".join(_texto_plano(t) for t in p["title"])
    if p.get("rich_text"): return "".join(_texto_plano(t) for t in p["rich_text"])
    if p.get("select"): return p["select"]["name"] if p["select"] else ""
    if p.get("status"): return p["status"]["name"] if p["status"] else ""
    if p.get("multi_select"): return [o["name"] for o in p["multi_select"]]
    if p.get("number") is not None: return p["number"]
    if p.get("date") and p["date"]: return p["date"]["start"]
    if p.get("relation"): return [r["id"] for r in p["relation"]]
    return ""


def q(db, **kw):
    """Compat: resuelve y consulta una sola pagina (usar query_all para todo)."""
    ds = _resolver_data_source(get_client(), db)
    return get_client().data_sources.query(data_source_id=ds, **kw)


def _poliza_desde_props(pid: str, pr: dict):
    car_raw = get_text(pr, E.POL_CARENCIA) or '{"default":0}'
    try: car = json.loads(car_raw)
    except (TypeError, ValueError): car = {"default": 0}
    fstr = get_text(pr, E.POL_FECHA_INICIO)
    if not fstr:
        print(f"{pid}: fecha_inicio vacía. Props: {list(pr.keys())}")
        return None
    return Poliza(pid, get_text(pr, E.POL_COBERTURA) or [],
        date.fromisoformat(str(fstr)[:10]),
        car, get_text(pr, E.POL_MONTO_MAX) or 0,
        get_text(pr, E.POL_MONTO_USADO) or 0,
        get_text(pr, E.POL_EXCLUSIONES) or [],
        get_text(pr, E.POL_SEGUNDA_OPINION) or [])


def fetch_poliza(paciente_id: str, dormir=time.sleep):
    """Consulta solo la poliza del paciente en lugar de cargarlas todas.

    Devuelve (poliza, page_id) para poder persistir la reserva (issue #15).
    """
    ds = data_sources_ids()["polizas"]
    pags = query_all(ds, dormir=dormir,
        filter={"property": E.POL_PACIENTE_ID, "title": {"equals": paciente_id}})
    for pg in pags:
        pol = _poliza_desde_props(paciente_id, pg["properties"])
        if pol:
            return pol, pg["id"]
    return None, None


def fetch_polizas(dormir=time.sleep):
    ds = data_sources_ids()["polizas"]
    out = {}
    for pg in query_all(ds, dormir=dormir):
        pr = pg["properties"]
        pid = get_text(pr, E.POL_PACIENTE_ID)
        if not pid:
            print(f"Fila sin paciente_id, salto. Props: {list(pr.keys())}")
            continue
        pol = _poliza_desde_props(pid, pr)
        if pol:
            out[pid] = pol
    return out


def resolucion_existe(informe_id: str, dormir=time.sleep) -> bool:
    """Idempotencia: comprueba si ya hay resolucion para ese informe."""
    ds = data_sources_ids()["resoluciones"]
    pags = query_all(ds, dormir=dormir,
        filter={"property": E.RES_INFORME, "relation": {"contains": informe_id}})
    return len(pags) > 0


def _autofill(notion, pg, pr, pid: str):
    """Rellena campos desde informe_texto. Devuelve el dict extraído (o None)."""
    txt = get_text(pr, E.INF_TEXTO)
    if not (txt and (not get_text(pr, E.INF_PROCEDIMIENTO) or not get_text(pr, E.INF_DOCUMENTOS))):
        return None
    ext = extraer_desde_texto(txt)
    upd = {}
    if ext.get("paciente_id") and not get_text(pr, E.INF_PACIENTE_ID):
        upd[E.INF_PACIENTE_ID] = {"title": [{"text": {"content": ext["paciente_id"]}}]}
    if ext.get("medico") and not get_text(pr, E.INF_MEDICO):
        upd[E.INF_MEDICO] = {"rich_text": [{"text": {"content": ext["medico"]}}]}
    if ext.get("costo") and not get_text(pr, E.INF_COSTO):
        upd[E.INF_COSTO] = {"number": ext["costo"]}
    if ext.get("urgencia") and not get_text(pr, E.INF_URGENCIA):
        upd[E.INF_URGENCIA] = {"select": {"name": ext["urgencia"]}}
    if ext["procedimiento"] and not get_text(pr, E.INF_PROCEDIMIENTO):
        upd[E.INF_PROCEDIMIENTO] = {"select": {"name": ext["procedimiento"]}}
    if ext["cie"] and not get_text(pr, E.INF_DIAGNOSTICO):
        upd[E.INF_DIAGNOSTICO] = {"rich_text": [{"text": {"content": ext["cie"]}}]}
    if ext["documentos"]:
        prev = set(get_text(pr, E.INF_DOCUMENTOS) or [])
        upd[E.INF_DOCUMENTOS] = {"multi_select": [{"name": d} for d in sorted(prev | set(ext["documentos"]))]}
    if not upd:
        return ext
    # La evaluacion lee `pr`: debe ver todo lo extraido, no solo lo que se guarda en Notion.
    pr.update(upd)
    try:
        con_reintentos(notion.pages.update, page_id=pg["id"], properties=upd)
    except APIResponseError as e:
        # ej. opción select/multi no existe: reintenta sin urgencia/documentos
        print(f"{pid}: autofill parcial ({e}), reintento sin select")
        upd.pop(E.INF_URGENCIA, None); pr.pop(E.INF_URGENCIA, None)
        try: con_reintentos(notion.pages.update, page_id=pg["id"], properties=upd)
        except APIResponseError as e2: print(f"{pid}: no se pudo rellenar: {e2}")
    print(f"{pid}: autofill -> {ext}")
    return ext


def procesar_informe(notion, ds_res: str, pg, autofill=True):
    """Procesa un informe: autofill, evaluar, crear resolucion y marcar estado."""
    pr = pg["properties"]
    pid = get_text(pr, E.INF_PACIENTE_ID)
    ext = _autofill(notion, pg, pr, pid) if autofill else None
    pid = get_text(pr, E.INF_PACIENTE_ID) or pid
    pol, pol_id = fetch_poliza(pid)
    if not pol:
        print(f"Sin póliza para {pid}")
        return
    inf = InformeMedico(pid, get_text(pr, E.INF_PROCEDIMIENTO),
        get_text(pr, E.INF_DIAGNOSTICO), get_text(pr, E.INF_MEDICO),
        get_text(pr, E.INF_URGENCIA) or E.URG_PROGRAMADA,
        get_text(pr, E.INF_DOCUMENTOS) or [],
        get_text(pr, E.INF_COSTO) or 0,
        # 6b/#33: la confianza y las citas solo viven en la extracción (sin columna Notion)
        confianza_extraccion=ext["confianza"] if ext is not None else None,
        citas_no_encontradas=ext["citas_no_encontradas"] if ext is not None else [],
        # #7: veredicto de coherencia de la IA; .get() tolera extracciones sin estas claves
        coherencia_diagnostico=ext.get("coherencia") if ext is not None else None,
        justificacion_coherencia=ext.get("justificacion_coherencia", "") if ext is not None else "",
        procedimiento_regex=ext.get("procedimiento_regex") or None if ext is not None else None)
    if resolucion_existe(pg["id"]):
        print(f"{pid}: resolucion ya existe, salto")
    else:
        r = evaluar(pol, inf)
        con_reintentos(notion.pages.create, parent={"data_source_id": ds_res}, properties={
            E.RES_PACIENTE_ID: {"title": [{"text": {"content": pid}}]},
            E.RES_DECISION: {"select": {"name": r["decision"].value}},
            E.RES_MOTIVO: {"rich_text": [{"text": {"content": r["motivo"][:2000]}}]},
            E.RES_FALTANTES: {"multi_select": [{"name": f} for f in r["faltantes"]]},
            E.RES_AUTORIZACION: {"rich_text": [{"text": {"content": r.get("autorizacion_id", "-")}}]},
            E.RES_INFORME: {"relation": [{"id": pg["id"]}]},
        })
        print(f"{pid}/{inf.procedimiento} -> {r['decision'].value}")
        if r["decision"].value == "PREAPROBADA" and pol_id:
            # issue #15: reservar el monto en la póliza para que la
            # siguiente evaluación lo tenga en cuenta
            con_reintentos(notion.pages.update, page_id=pol_id, properties={
                E.POL_MONTO_USADO: {"number": pol.monto_usado + inf.costo_estimado},
            })
    con_reintentos(notion.pages.update, page_id=pg["id"],
        properties={E.INF_ESTADO: {"status": {"name": E.ESTADO_PROCESADO}}})


def run_once(autofill=True):
    notion = get_client()
    ds = data_sources_ids()
    pendientes = query_all(ds["informes"],
        filter={"property": E.INF_ESTADO, "status": {"equals": E.ESTADO_PENDIENTE}})
    for pg in pendientes:
        try:
            procesar_informe(notion, ds["resoluciones"], pg, autofill)
        except Exception as e:  # un fallo no detiene al resto
            print(f"{pg.get('id')}: error, continuo con el siguiente ({e})")

if __name__ == "__main__":
    run_once()
