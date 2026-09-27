"""Datos sinteticos de la demo publica (issue #9).

- POLIZAS_DEMO: las mismas cuatro polizas que se cargan en Notion
  (scripts/cargar_polizas_demo.py), cada una pensada para un escenario.
- CASOS: los 10 informes sinteticos de tests/data/informes_sinteticos.json,
  con la poliza sugerida para que cada caso muestre lo que pretende.

La pagina y la API evaluan contra estas polizas en memoria: probar la demo
no escribe en Notion ni gasta el saldo de las polizas de Notion.
"""
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from preauth.reglas import Poliza

RUTA_INFORMES = Path(__file__).resolve().parent.parent / "tests" / "data" / "informes_sinteticos.json"


@dataclass(frozen=True)
class PolizaDemo:
    poliza: Poliza
    descripcion: str


POLIZAS_DEMO: dict[str, PolizaDemo] = {
    "P001": PolizaDemo(
        Poliza("P001", ["Colecistectomía", "Apendicectomía", "Hernia inguinal"], date(2024, 1, 1),
               {"default": 8}, 50000, 5000, exclusiones=["Rinoplastia estética"]),
        "Caso base: cubre colecistectomía, apendicectomía y hernia inguinal. Afiliada desde 2024.",
    ),
    "P002": PolizaDemo(
        Poliza("P002", ["Artroplastia", "Cataratas", "Hernia inguinal"], date(2025, 1, 15),
               {"Artroplastia": 12, "default": 6}, 80000, 0,
               requiere_segunda_opinion=["Artroplastia"]),
        "Artroplastia con segunda opinión obligatoria y 12 meses de carencia; también cataratas.",
    ),
    "P003": PolizaDemo(
        Poliza("P003", ["Colecistectomía", "Apendicectomía"], date(2026, 4, 26), {"default": 8}, 50000, 0),
        "Afiliación reciente: no cumple la carencia de 8 meses, salvo emergencia.",
    ),
    "P004": PolizaDemo(
        Poliza("P004", ["Colecistectomía", "Hernia inguinal"], date(2023, 6, 1), {"default": 6}, 10000, 9000),
        "Saldo casi agotado: quedan 1 000 de 10 000.",
    ),
}

# Poliza con la que cada informe sintetico muestra mejor su escenario.
POLIZA_SUGERIDA = {
    "S01": "P001", "S02": "P002", "S03": "P003", "S04": "P004", "S05": "P002",
    "S06": "P001", "S07": "P001", "S08": "P001", "S09": "P001", "S10": "P001",
}


def cargar_casos() -> list[dict]:
    """Casos para el selector de la pagina. Si el archivo no esta, la pagina sigue funcionando."""
    try:
        informes = json.loads(RUTA_INFORMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        {"id": c["id"], "descripcion": c["descripcion"], "texto": c["texto"],
         "poliza_id": POLIZA_SUGERIDA.get(c["id"], "P001")}
        for c in informes
    ]


CASOS = cargar_casos()
