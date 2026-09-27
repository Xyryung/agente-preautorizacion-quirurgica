"""Sincronizacion Notion <-> agente de pre-autorizacion.

Ejecutar desde la raiz del repo:  python -m preauth.notion_repo
"""
import json
from datetime import date
from functools import lru_cache

from notion_client import Client

from preauth.config import notion_settings
from preauth import esquema as E
from preauth.extraccion import extraer_desde_texto
from preauth.reglas import InformeMedico, Poliza, evaluar


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Crea el cliente de Notion la primera vez que se necesita, no al importar."""
    return Client(auth=notion_settings().token)

def get_text(props, name):
    p = props.get(name, {})
    if p.get("title"): return "".join(t["plain_text"] for t in p["title"])
    if p.get("rich_text"): return "".join(t["plain_text"] for t in p["rich_text"])
    if p.get("select"): return p["select"]["name"] if p["select"] else ""
    if p.get("status"): return p["status"]["name"] if p["status"] else ""
    if p.get("multi_select"): return [o["name"] for o in p["multi_select"]]
    if p.get("number") is not None: return p["number"]
    if p.get("date") and p["date"]: return p["date"]["start"]
    return ""

def q(db, **kw):
    notion = get_client()
    # Acepta database_id o data_source_id y resuelve automáticamente
    try:
        return notion.data_sources.query(data_source_id=db, **kw)
    except Exception:
        info = notion.databases.retrieve(database_id=db)
        ds = info.get("data_sources", [])
        if not ds:
            raise
        return notion.data_sources.query(data_source_id=ds[0]["id"], **kw)

def fetch_polizas():
    res = q(notion_settings().db_polizas)
    out = {}
    for pg in res["results"]:
        pr = pg["properties"]
        pid = get_text(pr, E.POL_PACIENTE_ID)
        if not pid:
            print(f"Fila sin paciente_id, salto. Props: {list(pr.keys())}")
            continue
        car_raw = get_text(pr, E.POL_CARENCIA) or '{"default":0}'
        try: car = json.loads(car_raw)
        except: car = {"default": 0}
        fstr = get_text(pr, E.POL_FECHA_INICIO)
        if not fstr:
            print(f"{pid}: fecha_inicio vacía. Props: {list(pr.keys())}")
            continue
        out[pid] = Poliza(pid, get_text(pr, E.POL_COBERTURA) or [],
            date.fromisoformat(str(fstr)[:10]),
            car, get_text(pr, E.POL_MONTO_MAX) or 0,
            get_text(pr, E.POL_MONTO_USADO) or 0,
            get_text(pr, E.POL_EXCLUSIONES) or [],
            get_text(pr, E.POL_SEGUNDA_OPINION) or [])
    return out

def run_once(autofill=True):
    notion = get_client()
    cfg = notion_settings()
    polizas = fetch_polizas()
    pend = q(cfg.db_informes,
        filter={"property": E.INF_ESTADO, "status": {"equals": E.ESTADO_PENDIENTE}})
    for pg in pend["results"]:
        pr = pg["properties"]
        pid = get_text(pr, E.INF_PACIENTE_ID)
        # --- AUTOFILL: si hay informe_texto y faltan campos, extraer y rellenar ---
        if autofill:
            txt = get_text(pr, E.INF_TEXTO)
            if txt and (not get_text(pr, E.INF_PROCEDIMIENTO) or not get_text(pr, E.INF_DOCUMENTOS)):
                ext = extraer_desde_texto(txt)
                upd = {}
                if ext.get("paciente_id") and not get_text(pr, E.INF_PACIENTE_ID):
                    upd[E.INF_PACIENTE_ID] = {"title": [{"text": {"content": ext["paciente_id"]}}]}
                    pr[E.INF_PACIENTE_ID] = {"title": [{"text": {"content": ext["paciente_id"]}}]}
                if ext.get("medico") and not get_text(pr, E.INF_MEDICO):
                    upd[E.INF_MEDICO] = {"rich_text": [{"text": {"content": ext["medico"]}}]}
                if ext.get("costo") and not get_text(pr, E.INF_COSTO):
                    upd[E.INF_COSTO] = {"number": ext["costo"]}
                if ext.get("urgencia") and not get_text(pr, E.INF_URGENCIA):
                    upd[E.INF_URGENCIA] = {"select": {"name": ext["urgencia"]}}
                    pr[E.INF_URGENCIA] = {"select": {"name": ext["urgencia"]}}
                if ext["procedimiento"] and not get_text(pr, E.INF_PROCEDIMIENTO):
                    upd[E.INF_PROCEDIMIENTO] = {"select": {"name": ext["procedimiento"]}}
                if ext["cie"] and not get_text(pr, E.INF_DIAGNOSTICO):
                    upd[E.INF_DIAGNOSTICO] = {"rich_text": [{"text": {"content": ext["cie"]}}]}
                if ext["documentos"]:
                    prev = set(get_text(pr, E.INF_DOCUMENTOS) or [])
                    upd[E.INF_DOCUMENTOS] = {"multi_select": [{"name": d} for d in sorted(prev | set(ext["documentos"]))]}
                    pr[E.INF_DOCUMENTOS] = {"multi_select": [{"name": d} for d in sorted(prev | set(ext["documentos"]))]}
                if upd:
                    if E.INF_PROCEDIMIENTO in upd: pr[E.INF_PROCEDIMIENTO] = {"select": {"name": ext["procedimiento"]}}
                    try:
                        notion.pages.update(page_id=pg["id"], properties=upd)
                    except Exception as e:
                        # ej. opción select/multi no existe: reintenta sin urgencia/documentos
                        print(f"{pid}: autofill parcial ({e}), reintento sin select")
                        upd.pop(E.INF_URGENCIA, None); pr.pop(E.INF_URGENCIA, None)
                        try: notion.pages.update(page_id=pg["id"], properties=upd)
                        except Exception as e2: print(f"{pid}: no se pudo rellenar: {e2}")
                    print(f"{pid}: autofill -> {ext}")
        pid = get_text(pr, E.INF_PACIENTE_ID) or pid
        pol = polizas.get(pid)
        if not pol:
            print(f"Sin póliza para {pid}"); continue
        inf = InformeMedico(pid, get_text(pr, E.INF_PROCEDIMIENTO),
            get_text(pr, E.INF_DIAGNOSTICO), get_text(pr, E.INF_MEDICO),
            get_text(pr, E.INF_URGENCIA) or E.URG_PROGRAMADA,
            get_text(pr, E.INF_DOCUMENTOS) or [],
            get_text(pr, E.INF_COSTO) or 0)
        r = evaluar(pol, inf)
        notion.pages.create(parent={"data_source_id": cfg.db_resoluciones}, properties={
            E.RES_PACIENTE_ID: {"title": [{"text": {"content": pid}}]},
            E.RES_DECISION: {"select": {"name": r["decision"].value}},
            E.RES_MOTIVO: {"rich_text": [{"text": {"content": r["motivo"][:2000]}}]},
            E.RES_FALTANTES: {"multi_select": [{"name": f} for f in r["faltantes"]]},
            E.RES_AUTORIZACION: {"rich_text": [{"text": {"content": r.get("autorizacion_id", "-")}}]},
        })
        notion.pages.update(page_id=pg["id"], properties={E.INF_ESTADO: {"status": {"name": E.ESTADO_PROCESADO}}})
        print(f"{pid}/{inf.procedimiento} -> {r['decision'].value}")

if __name__ == "__main__":
    run_once()
