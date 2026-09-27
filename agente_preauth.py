"""
Agente de Pre-Autorización Quirúrgica en Tiempo Real
Flujo: Notion DB (Informe Hospital + Póliza) -> Agente IA -> Decisión instantánea
"""
from dataclasses import dataclass, field
from datetime import date
from typing import List, Literal
from enum import Enum

class Decision(str, Enum):
    PREAPROBADA = "PREAPROBADA"
    DOCUMENTOS_FALTANTES = "SOLICITUD_DOCUMENTOS_FALTANTES"
    DENEGADA = "DENEGADA"

@dataclass
class Poliza:
    paciente_id: str
    cobertura_procedimientos: List[str]  # códigos CIE-9/CPT cubiertos, ej ["Colecistectomía", "Apéndice"]
    fecha_inicio: date
    carencia_meses: dict = field(default_factory=dict)  # {"Colecistectomía": 8, "default": 0}
    monto_maximo: float = 100000
    monto_usado: float = 0
    exclusiones: List[str] = field(default_factory=list)
    requiere_segunda_opinion: List[str] = field(default_factory=list)

@dataclass
class InformeMedico:
    paciente_id: str
    procedimiento: str
    diagnostico_cie10: str
    medico: str
    urgencia: Literal["emergencia", "programada"] = "programada"
    documentos_adjuntos: List[str] = field(default_factory=list)
    # docs requeridos base
    costo_estimado: float = 0

DOCS_BASE = ["identificacion", "informe_medico", "consentimiento"]
DOCS_POR_PROCEDIMIENTO = {
    "Colecistectomía": ["ecografia_abdominal", "analitica"],
    "Artroplastia": ["radiografia", "segunda_opinion", "analitica"],
    "default": ["presupuesto_hospital"],
}

def meses_afiliado(poliza: Poliza, hoy: date) -> int:
    return (hoy.year - poliza.fecha_inicio.year) * 12 + (hoy.month - poliza.fecha_inicio.month)

def evaluar(poliza: Poliza, informe: InformeMedico, hoy: date = date.today()) -> dict:
    faltantes, motivos = [], []

    # 1. Cobertura
    if informe.procedimiento not in poliza.cobertura_procedimientos:
        return {"decision": Decision.DENEGADA, "motivo": f"Procedimiento '{informe.procedimiento}' no cubierto por póliza.", "faltantes": []}
    if informe.procedimiento in poliza.exclusiones:
        return {"decision": Decision.DENEGADA, "motivo": "Procedimiento en lista de exclusiones.", "faltantes": []}

    # 2. Carencia (se omite en emergencia)
    if informe.urgencia != "emergencia":
        carencia_req = poliza.carencia_meses.get(informe.procedimiento, poliza.carencia_meses.get("default", 0))
        antiguedad = meses_afiliado(poliza, hoy)
        if antiguedad < carencia_req:
            return {"decision": Decision.DENEGADA,
                    "motivo": f"No cumple carencia: {antiguedad}/{carencia_req} meses.",
                    "faltantes": []}

    # 3. Monto
    if poliza.monto_usado + informe.costo_estimado > poliza.monto_maximo:
        return {"decision": Decision.DENEGADA, "motivo": "Excede monto máximo de póliza.", "faltantes": []}

    # 4. Documentos
    requeridos = set(DOCS_BASE + DOCS_POR_PROCEDIMIENTO.get(informe.procedimiento, DOCS_POR_PROCEDIMIENTO["default"]))
    if informe.procedimiento in poliza.requiere_segunda_opinion:
        requeridos.add("segunda_opinion")
    faltantes = [d for d in requeridos if d not in informe.documentos_adjuntos]

    if faltantes:
        return {"decision": Decision.DOCUMENTOS_FALTANTES,
                "motivo": "Faltan documentos para pre-aprobar.",
                "faltantes": faltantes}

    return {"decision": Decision.PREAPROBADA,
            "motivo": "Cumple cobertura, carencia y documentación. Pre-aprobación emitida.",
            "faltantes": [],
            "autorizacion_id": f"AUT-{informe.paciente_id}-{hoy.strftime('%Y%m%d')}"}

# ---- Integración Notion (pseudo-código listo para notion-client) ----
NOTION_SCHEMA = {
    "db_informes": ["paciente_id", "procedimiento", "diagnostico_cie10", "urgencia", "documentos", "costo_estimado", "estado"],
    "db_polizas": ["paciente_id", "cobertura", "fecha_inicio", "carencia", "monto_maximo", "monto_usado"],
    "db_resoluciones": ["paciente_id", "decision", "motivo", "faltantes", "autorizacion_id", "timestamp"]
}
"""
Integración real:
  pip install notion-client
  from notion_client import Client
  notion = Client(auth=os.environ["NOTION_TOKEN"])
  - Leer páginas con database.query(db_informes, filter={"property":"estado","select":{"equals":"pendiente"}})
  - Mapear a dataclasses, llamar evaluar(), y escribir en db_resoluciones + actualizar estado.
  - Disparar con webhook / polling cada 60s -> tiempo real (<10s por caso).
"""

if __name__ == "__main__":
    p = Poliza("P001", ["Colecistectomía"], date(2024, 1, 1), {"default": 8}, 50000, 5000)
    casos = [
        InformeMedico("P001", "Colecistectomía", "K80", "Dr. Gil",
                      documentos_adjuntos=["identificacion","informe_medico","consentimiento","ecografia_abdominal","analitica","presupuesto_hospital"],
                      costo_estimado=8000),
        InformeMedico("P001", "Colecistectomía", "K80", "Dr. Gil",
                      documentos_adjuntos=["identificacion","informe_medico"], costo_estimado=8000),
        InformeMedico("P001", "Rinoplastia estética", "Z41", "Dr. Gil",
                      documentos_adjuntos=["identificacion"], costo_estimado=5000),
    ]
    for i, c in enumerate(casos, 1):
        print(f"Caso {i}:", evaluar(p, c, date(2026, 9, 26)))
