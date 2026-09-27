"""Extraccion de datos del informe medico.

Dos caminos, que el issue #6 unificara detras de LLM_PROVIDER:
- extraer_desde_texto(): reglas con expresiones regulares (sin IA).
- extraer_con_ia(): LLM local via Ollama (origen: llm_local.py).
"""
import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

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

# ---------------------------------------------------------------------------
# Contrato de la extraccion (issue #6)
# ---------------------------------------------------------------------------
MAX_CARACTERES = 8000   # los informes mas largos se recortan antes de enviarlos
DESCONOCIDO = "desconocido"
NO_INDICADA = "no_indicada"
DOCUMENTOS_CONOCIDOS = list(DOC_KEYWORDS)

# Los Literal se construyen desde las listas de arriba: una sola fuente de verdad.
Procedimiento = Literal[tuple(PROCEDIMIENTOS_CONOCIDOS + [DESCONOCIDO])]
Documento = Literal[tuple(DOCUMENTOS_CONOCIDOS)]
Urgencia = Literal["emergencia", "programada", NO_INDICADA]


class Evidencia(BaseModel):
    campo: str   # "procedimiento", "urgencia", "costo_estimado", ...
    cita: str    # fragmento textual breve del informe que justifica el valor


class ExtraccionInforme(BaseModel):
    """Lo que debe devolver el modelo. Los Literal le obligan a elegir de nuestras listas."""
    procedimiento: Procedimiento
    cie10: str | None
    urgencia: Urgencia
    costo_estimado: float | None
    paciente_id: str | None
    medico: str | None
    documentos_aportados: list[Documento]
    documentos_pendientes: list[Documento]
    evidencia: list[Evidencia]
    confianza: float


@dataclass(frozen=True)
class ResultadoExtraccion:
    datos: ExtraccionInforme
    proveedor: str                  # el que realmente produjo 'datos': "openai" o "regex"
    latencia_ms: float
    error: str | None = None        # por que se uso el respaldo, si se uso
    advertencias: tuple[str, ...] = ()


def extraer(texto: str, *, proveedor: str | None = None, cliente=None) -> ResultadoExtraccion:
    """Punto de entrada de la extraccion. Se implementa en el siguiente commit."""
    raise NotImplementedError("extraer() se implementa en el issue #6")

def extraer_desde_texto(texto: str) -> dict:
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
    return {"procedimiento": proc, "cie": m.group(0) if m else "", "documentos": docs, "urgencia": urg,
            "paciente_id": mp.group(0) if mp else "",
            "medico": mm.group(0).strip() if mm else "",
            "costo": costo}

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
