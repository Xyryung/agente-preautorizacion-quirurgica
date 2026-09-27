"""Esqueleto del servicio publico (walking skeleton).

Objetivo: tener un enlace publico funcionando desde el primer dia.
Ejecuta el motor de reglas actual con tres casos de demostracion fijos.
Los issues de API, IA y Notion lo iran reemplazando por el flujo real.

Local:   uvicorn app.main:app --reload
Render:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
import html
import time
from datetime import date

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from preauth.reglas import InformeMedico, Poliza, evaluar

app = FastAPI(title="Agente de Pre-Autorización Quirúrgica", version="0.1.0")

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

CLASE_DECISION = {
    "PREAPROBADA": "ok",
    "SOLICITUD_DOCUMENTOS_FALTANTES": "pendiente",
    "DENEGADA": "denegada",
}


def ejecutar_demo() -> list[dict]:
    # 'hoy' se pasa explicito: el valor por defecto de evaluar() se congela
    # al importar el modulo, y en un servidor eso es un error (ver issues).
    hoy = date.today()
    resultados = []
    for nombre, informe in CASOS_DEMO:
        inicio = time.perf_counter()
        r = evaluar(POLIZA_DEMO, informe, hoy)
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
    return ejecutar_demo()


PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pre-autorización quirúrgica</title>
<style>
  :root { --tinta: #1c2b33; --suave: #5b6b73; --linea: #d5dde1;
          --ok: #1e7a4f; --pend: #9a6200; --no: #b3261e; }
  body { font-family: system-ui, "Segoe UI", sans-serif; color: var(--tinta);
         max-width: 60rem; margin: 2.5rem auto; padding: 0 1.25rem; line-height: 1.5; }
  h1 { font-size: 1.6rem; margin-bottom: .25rem; }
  p { color: var(--suave); max-width: 65ch; }
  .tabla { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; margin-top: 1.5rem; }
  th, td { text-align: left; padding: .6rem .75rem; border-bottom: 1px solid var(--linea);
           vertical-align: top; }
  th { font-weight: 600; font-size: .9rem; color: var(--suave); }
  .ok { color: var(--ok); font-weight: 600; }
  .pendiente { color: var(--pend); font-weight: 600; }
  .denegada { color: var(--no); font-weight: 600; }
</style>
</head>
<body>
<h1>Agente de pre-autorización quirúrgica</h1>
<p>Versión preliminar: tres casos sintéticos evaluados en vivo por el motor de reglas.
Todos los datos son ficticios. La versión completa leerá informes y pólizas desde Notion.</p>
<div class="tabla">
<table>
<thead><tr><th>Caso</th><th>Procedimiento</th><th>Decisión</th><th>Motivo</th>
<th>Faltantes</th><th>Latencia</th></tr></thead>
<tbody>__FILAS__</tbody>
</table>
</div>
<p>Resultados en JSON en <a href="/api/demo">/api/demo</a>. Documentación interactiva de la API en <a href="/docs">/docs</a>.</p>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    filas = []
    for r in ejecutar_demo():
        e = {k: html.escape(str(v)) for k, v in r.items() if k != "faltantes"}
        faltantes = html.escape(", ".join(r["faltantes"])) or "-"
        clase = CLASE_DECISION.get(r["decision"], "")
        filas.append(
            f"<tr><td>{e['caso']}</td><td>{e['procedimiento']}</td>"
            f"<td class='{clase}'>{e['decision']}</td><td>{e['motivo']}</td>"
            f"<td>{faltantes}</td><td>{e['latencia_ms']} ms</td></tr>"
        )
    return PAGINA.replace("__FILAS__", "".join(filas))
