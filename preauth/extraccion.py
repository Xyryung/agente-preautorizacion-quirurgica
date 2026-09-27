"""Extraccion de datos del informe medico.

Dos caminos, que el issue #6 unificara detras de LLM_PROVIDER:
- extraer_desde_texto(): reglas con expresiones regulares (sin IA).
- extraer_con_ia(): LLM local via Ollama (origen: llm_local.py).
"""
import json
import re
import urllib.request

from preauth.texto import norm

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
    from preauth.reglas import DOCS_BASE, DOCS_POR_PROCEDIMIENTO
    t = norm(texto) 
    proc = next((p for p in PROCEDIMIENTOS_CONOCIDOS if norm(p) in t), "")
    m = CIE_RE.search(texto or "")
    docs = [d for d, kws in DOC_KEYWORDS.items() if any(norm(k) in t for k in kws)]
    urg = "emergencia" if any(w in t for w in ["urgente", "emergencia", "emergency"]) else "programada"
    mp = PID_RE.search(texto or "")
    mm = MED_RE.search(texto or "")
    mc = COST_RE.search(texto or "")
    costo = None
    if mc:
        costo = int(next(g for g in mc.groups() if g))
    requeridos = DOCS_BASE + DOCS_POR_PROCEDIMIENTO.get(proc, DOCS_POR_PROCEDIMIENTO["default"])
    return {"procedimiento": proc, "cie": m.group(0) if m else "", "documentos": docs, "urgencia": urg,
            "paciente_id": mp.group(0) if mp else "",
            "medico": mm.group(0).strip() if mm else "",
            "costo": costo,
            # 6b: heuristica regex (el extractor IA la refina): sin procedimiento
            # reconocible la confianza baja y todo lo requerido queda sin citar.
            "confianza": 0.8 if proc else 0.3,
            "citas_no_encontradas": sorted(set(requeridos) - set(docs))}

SYSTEM = """Eres extractor de informes quirúrgicos. Devuelve SOLO JSON válido:
{"procedimiento": str, "cie": str, "urgencia": "programada|emergencia", "documentos": [str], "costo_estimado": null}
Procedimientos: Colecistectomía, Artroplastia, Apendicectomía, Cataratas, Hernia inguinal, Rinoplastia estética.
Documentos: identificacion, informe_medico, consentimiento, ecografia_abdominal, analitica, radiografia, segunda_opinion, presupuesto_hospital."""

def extraer_con_ia(texto: str, model="gpt-oss:20b") -> dict:
    body = json.dumps({"model": model, "stream": False,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": texto}]}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    raw = out["message"]["content"].strip()
    # limpia fences ```json ... ```
    if "```" in raw:
        raw = raw.split("```")[1] if raw.count("```") >= 2 else raw.replace("```", "")
        raw = raw.replace("json", "", 1).strip()
    # recorta desde primer { hasta último }
    raw = raw[raw.find("{"):raw.rfind("}")+1]
    return json.loads(raw)
