"""Servicio publico del agente de pre-autorizacion quirurgica.

- /                         pagina para probar el agente con casos o informes propios (#9)
- POST /api/evaluar         evalua un informe (texto libre o estructurado) contra una poliza (#9)
- POST /api/guardar-informe guarda el informe en Notion como pendiente (#34)
- POST /webhook/notion      dispara el agente cuando cambia Notion (#12)
- /health                   health check de Render

Local:   uvicorn app.main:app --reload
Render:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
import json
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import api_evaluar, api_guardar, proteccion, webhook_notion
from app.casos_demo import CASOS, POLIZAS_DEMO
from preauth.reglas import InformeMedico, Poliza, evaluar

app = FastAPI(
    title="Agente de Pre-Autorización Quirúrgica",
    version="1.0.0",
    description="La IA extrae los datos del informe médico; las reglas de la póliza deciden. "
                "Todos los datos son sintéticos.",
)
proteccion.instalar(app)
app.include_router(api_evaluar.router)
app.include_router(api_guardar.router)
app.include_router(webhook_notion.router)

POLIZA_DEMO = Poliza("P001", ["Colecistectomía"], date(2024, 1, 1), {"default": 8}, 50000, 5000)

DOCS_COMPLETOS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]

CASOS_DEMO = [
    ("Documentación completa",
     InformeMedico("P001", "Colecistectomía", "K80", "Dr. Gil",
                   documentos_adjuntos=DOCS_COMPLETOS, costo_estimado=8000)),
    ("Faltan documentos",
     InformeMedico("P001", "Colecistectomía", "K80", "Dr. Gil",
                   documentos_adjuntos=["identificacion", "informe_medico"], costo_estimado=8000)),
    ("Procedimiento no cubierto",
     InformeMedico("P001", "Rinoplastia estética", "Z41", "Dr. Gil",
                   documentos_adjuntos=["identificacion"], costo_estimado=5000)),
]


def ejecutar_demo() -> list[dict]:
    resultados = []
    for nombre, informe in CASOS_DEMO:
        inicio = time.perf_counter()
        r = evaluar(POLIZA_DEMO, informe)
        resultados.append({
            "caso": nombre,
            "procedimiento": informe.procedimiento,
            "decision": r["decision"].value,
            "motivo": r["motivo"],
            "faltantes": sorted(r["faltantes"]),
            "latencia_ms": round((time.perf_counter() - inicio) * 1000, 3),
        })
    return resultados


@app.get("/health")
def health() -> dict:
    """Usado por Render para saber si el servicio esta vivo."""
    return {"status": "ok"}


@app.get("/api/demo")
def api_demo() -> list[dict]:
    """Tres casos estructurados fijos, evaluados en vivo por el motor de reglas."""
    return ejecutar_demo()


def _resumen(p: Poliza) -> str:
    return f"{', '.join(p.cobertura_procedimientos)} (saldo {p.monto_maximo - p.monto_usado:,.0f})"


def _campos(p: Poliza) -> dict:
    """Los mismos nombres que PolizaEntrada: la pagina los edita y los envia tal cual."""
    return {
        "paciente_id": p.paciente_id, "cobertura": p.cobertura_procedimientos,
        "fecha_inicio": p.fecha_inicio.isoformat(), "carencia_meses": p.carencia_meses,
        "monto_maximo": p.monto_maximo, "monto_usado": p.monto_usado,
        "exclusiones": p.exclusiones, "requiere_segunda_opinion": p.requiere_segunda_opinion,
    }


def _datos_pagina() -> str:
    datos = {
        "polizas": {pid: {"resumen": _resumen(d.poliza), "descripcion": d.descripcion,
                          "campos": _campos(d.poliza)}
                    for pid, d in POLIZAS_DEMO.items()},
        "casos": CASOS,
    }
    # "</" cerraria el <script> que contiene el JSON.
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


PAGINA = (Path(__file__).parent / "pagina.html").read_text(encoding="utf-8").replace("__DATOS__", _datos_pagina())


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGINA
