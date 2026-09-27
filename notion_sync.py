"""Sincronización Notion <-> Agente Pre-Autorización. pip install notion-client"""
import os, json
from datetime import date, datetime
from notion_client import Client
from agente_preauth import Poliza, InformeMedico, evaluar

notion = Client(auth=os.environ["NOTION_TOKEN"])
DB_INF = os.environ["NOTION_DB_INFORMES"]
DB_POL = os.environ["NOTION_DB_POLIZAS"]
DB_RES = os.environ["NOTION_DB_RESOLUCIONES"]

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
    # Acepta database_id o data_source_id y resuelve automáticamente
    try:
        return notion.data_sources.query(data_source_id=db, **kw)
    except Exception:
        info = notion.databases.retrieve(database_id=db)
        ds = info.get("data_sources", [])
        if not ds:
            raise
        return notion.data_sources.query(data_source_id=ds[0]["id"], **kw)

def get2(pr, *names):
    for n in names:
        v = get_text(pr, n)
        if v or v == 0:
            return v
    return ""

def fetch_polizas():
    res = q(DB_POL)
    out = {}
    for pg in res["results"]:
        pr = pg["properties"]
        pid = get2(pr, "paciente_id")
        if not pid:
            print(f"Fila sin paciente_id, salto. Props: {list(pr.keys())}")
            continue
        car_raw = get2(pr, "carencia", "carencia ") or '{"default":0}'
        try: car = json.loads(car_raw)
        except: car = {"default": 0}
        fstr = get2(pr, "fecha_inicio")
        if not fstr:
            print(f"{pid}: fecha_inicio vacía. Props: {list(pr.keys())}")
            continue
        out[pid] = Poliza(pid, get2(pr, "cobertura") or [],
            date.fromisoformat(str(fstr)[:10]),
            car, get2(pr, "monto maximo", "monto_maximo") or 0,
            get2(pr, "monto usado", "monto_usado") or 0,
            get2(pr, "exclusiones") or [],
            get2(pr, "requiere_segunda_opinion") or [])
    return out

import re
from agente_preauth import DOCS_POR_PROCEDIMIENTO, DOCS_BASE

PROCEDIMIENTOS_CONOCIDOS = ["Colecistectomía", "Artroplastia", "Apendicectomía", "Cataratas", "Hernia inguinal", "Rinoplastia estética"]
CIE_RE = re.compile(r"\b[A-Z]\d{2}(?:\.\d)?\b")
PID_RE = re.compile(r"\bP\d{3}\b")
MED_RE = re.compile(r"\b[Dd]r[a]?\.?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)")
COST_RE = re.compile(r"(?:por\s+|costo\s*|presupuesto[^0-9]{0,20})(\d{3,6})|\b(\d{3,6})\s*(?:€|\$|USD|euros?)", re.I)
DOC_KEYWORDS = {"identificacion": ["dni", "identificacion", "cedula"], "informe_medico": ["informe"],
    "consentimiento": ["consentimiento"], "ecografia_abdominal": ["ecografia"],
    "analitica": ["analitica", "analítica", "sangre"], "radiografia": ["radiografia"],
    "segunda_opinion": ["segunda opinion"], "presupuesto_hospital": ["presupuesto", "costo", "€", "$"]}

def extraer_desde_texto(texto: str) -> dict:
    t = (texto or "").lower()
    proc = next((p for p in PROCEDIMIENTOS_CONOCIDOS if p.lower() in t), "")
    m = CIE_RE.search(texto or "")
    docs = [d for d, kws in DOC_KEYWORDS.items() if any(k in t for k in kws)]
    urg = "emergencia" if any(w in t for w in ["urgente", "emergencia", "emergency"]) else "programada"
    mp = PID_RE.search(texto or "")
    mm = MED_RE.search(texto or "")
    mc = COST_RE.search(texto or "")
    costo = None
    if mc:
        costo = int(next(g for g in mc.groups() if g))
    return {"procedimiento": proc, "cie": m.group(0) if m else "", "documentos": docs, "urgencia": urg,
            "paciente_id": mp.group(0) if mp else "",
            "medico": mm.group(0).strip() if mm else "",
            "costo": costo}

def run_once(autofill=True):
    polizas = fetch_polizas()
    pend = q(DB_INF,
        filter={"property": "estado", "status": {"equals": "pendiente"}})
    for pg in pend["results"]:
        pr = pg["properties"]
        pid = get_text(pr, "paciente_id")
        # --- AUTOFILL: si hay informe_texto y faltan campos, extraer y rellenar ---
        if autofill:
            txt = get_text(pr, "informe_texto")
            if txt and (not get_text(pr, "procedimiento") or not get_text(pr, "documentos")):
                ext = extraer_desde_texto(txt)
                upd = {}
                if ext.get("paciente_id") and not get_text(pr, "paciente_id"):
                    upd["paciente_id"] = {"title": [{"text": {"content": ext["paciente_id"]}}]}
                    pr["paciente_id"] = {"title": [{"text": {"content": ext["paciente_id"]}}]}
                if ext.get("medico") and not get_text(pr, "medico"):
                    upd["medico"] = {"rich_text": [{"text": {"content": ext["medico"]}}]}
                if ext.get("costo") and not get_text(pr, "costo estimado"):
                    upd["costo estimado"] = {"number": ext["costo"]}
                if ext.get("urgencia") and not get_text(pr, "urgencia"):
                    upd["urgencia"] = {"select": {"name": ext["urgencia"]}}
                    pr["urgencia"] = {"select": {"name": ext["urgencia"]}}
                if ext["procedimiento"] and not get_text(pr, "procedimiento"):
                    upd["procedimiento"] = {"select": {"name": ext["procedimiento"]}}
                if ext["cie"] and not get_text(pr, "diagnostico_cie10"):
                    upd["diagnostico_cie10"] = {"rich_text": [{"text": {"content": ext["cie"]}}]}
                if ext["documentos"]:
                    prev = set(get_text(pr, "documentos") or [])
                    upd["documentos"] = {"multi_select": [{"name": d} for d in sorted(prev | set(ext["documentos"]))]}
                    pr["documentos"] = {"multi_select": [{"name": d} for d in sorted(prev | set(ext["documentos"]))]}
                if upd:
                    if "procedimiento" in upd: pr["procedimiento"] = {"select": {"name": ext["procedimiento"]}}
                    try:
                        notion.pages.update(page_id=pg["id"], properties=upd)
                    except Exception as e:
                        # ej. opción select/multi no existe: reintenta sin urgencia/documentos
                        print(f"{pid}: autofill parcial ({e}), reintento sin select")
                        upd.pop("urgencia", None); pr.pop("urgencia", None)
                        try: notion.pages.update(page_id=pg["id"], properties=upd)
                        except Exception as e2: print(f"{pid}: no se pudo rellenar: {e2}")
                    print(f"{pid}: autofill -> {ext}")
        pid = get_text(pr, "paciente_id") or pid
        pol = polizas.get(pid)
        if not pol:
            print(f"Sin póliza para {pid}"); continue
        inf = InformeMedico(pid, get_text(pr, "procedimiento"),
            get_text(pr, "diagnostico_cie10"), get_text(pr, "medico"),
            get_text(pr, "urgencia") or "programada",
            get_text(pr, "documentos") or [],
            get_text(pr, "costo_estimado") or 0)
        r = evaluar(pol, inf)
        notion.pages.create(parent={"data_source_id": DB_RES}, properties={
            "paciente_id": {"title": [{"text": {"content": pid}}]},
            "decision": {"select": {"name": r["decision"].value}},
            "motivo": {"rich_text": [{"text": {"content": r["motivo"][:2000]}}]},
            "faltantes": {"multi_select": [{"name": f} for f in r["faltantes"]]},
            "autorizacion_id": {"rich_text": [{"text": {"content": r.get("autorizacion_id", "-")}}]},
        })
        notion.pages.update(page_id=pg["id"], properties={"estado": {"status": {"name": "Listo"}}})
        print(f"{pid}/{inf.procedimiento} -> {r['decision'].value}")

if __name__ == "__main__":
    run_once()
