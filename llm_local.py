"""Extracción con IA local (Ollama). Sin API key. Requiere: ollama pull llama3.2:3b"""
import json, urllib.request

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
