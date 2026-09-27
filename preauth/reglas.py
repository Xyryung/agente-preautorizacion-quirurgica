"""
Agente de Pre-Autorización Quirúrgica en Tiempo Real
Flujo: Notion DB (Informe Hospital + Póliza) -> Agente IA -> Decisión instantánea
"""
from dataclasses import dataclass, field
from datetime import date
from typing import List, Literal
from enum import Enum
from preauth.texto import norm

class Decision(str, Enum):
    PREAPROBADA = "PREAPROBADA"
    DOCUMENTOS_FALTANTES = "SOLICITUD_DOCUMENTOS_FALTANTES"
    REVISION_MANUAL = "REVISION_MANUAL"
    DENEGADA = "DENEGADA"


class Resultado(str, Enum):
    """Resultado de UNA regla. La decision final se deriva de todos (ver decidir())."""
    CUMPLE = "cumple"
    INFORMATIVO = "informativo"          # no cambia la decision; se muestra en el motivo
    FALTAN_DOCUMENTOS = "faltan_documentos"
    REVISION = "revision"
    NO_CUMPLE = "no_cumple"


@dataclass(frozen=True)
class Hallazgo:
    regla: str            # "datos", "cobertura", "carencia", "monto", "documentos"
    resultado: Resultado
    mensaje: str


# De mayor a menor prioridad: el primer resultado presente define la decision.
PRECEDENCIA = [
    (Resultado.NO_CUMPLE, Decision.DENEGADA),
    (Resultado.REVISION, Decision.REVISION_MANUAL),
    (Resultado.FALTAN_DOCUMENTOS, Decision.DOCUMENTOS_FALTANTES),
]


def decidir(hallazgos: List[Hallazgo]) -> Decision:
    """DENEGADA > REVISION_MANUAL > DOCUMENTOS_FALTANTES > PREAPROBADA."""
    presentes = {h.resultado for h in hallazgos}
    for resultado, decision in PRECEDENCIA:
        if resultado in presentes:
            return decision
    return Decision.PREAPROBADA

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

def _contiene(lista: List[str], valor: str) -> bool:
    """True si 'valor' esta en 'lista', comparando con norm()."""
    objetivo = norm(valor)
    return any(norm(elemento) == objetivo for elemento in lista)


def _buscar(tabla: dict, clave: str, por_defecto):
    """Como tabla.get(clave, por_defecto), comparando las claves con norm()."""
    objetivo = norm(clave)
    for k, v in tabla.items():
        if norm(k) == objetivo:
            return v
    return por_defecto

def meses_afiliado(poliza: Poliza, hoy: date) -> int:
    return (hoy.year - poliza.fecha_inicio.year) * 12 + (hoy.month - poliza.fecha_inicio.month)

def evaluar(poliza: Poliza, informe: InformeMedico, hoy: date = date.today()) -> dict:
    faltantes, motivos = [], []

    # 1. Cobertura
    if not _contiene(poliza.cobertura_procedimientos, informe.procedimiento):
        return {"decision": Decision.DENEGADA, "motivo": f"Procedimiento '{informe.procedimiento}' no cubierto por póliza.", "faltantes": []}
    if _contiene(poliza.exclusiones, informe.procedimiento):
        return {"decision": Decision.DENEGADA, "motivo": "Procedimiento en lista de exclusiones.", "faltantes": []}

    # 2. Carencia (se omite en emergencia)
    if norm(informe.urgencia) != "emergencia":
        carencia_req = _buscar(poliza.carencia_meses, informe.procedimiento,
                       _buscar(poliza.carencia_meses, "default", 0))
        antiguedad = meses_afiliado(poliza, hoy)
        if antiguedad < carencia_req:
            return {"decision": Decision.DENEGADA,
                    "motivo": f"No cumple carencia: {antiguedad}/{carencia_req} meses.",
                    "faltantes": []}

    # 3. Monto
    if poliza.monto_usado + informe.costo_estimado > poliza.monto_maximo:
        return {"decision": Decision.DENEGADA, "motivo": "Excede monto máximo de póliza.", "faltantes": []}

    # 4. Documentos
    requeridos = set(DOCS_BASE + _buscar(DOCS_POR_PROCEDIMIENTO, informe.procedimiento, DOCS_POR_PROCEDIMIENTO["default"]))
    if _contiene(poliza.requiere_segunda_opinion, informe.procedimiento):
        requeridos.add("segunda_opinion")
    adjuntos = {norm(d) for d in informe.documentos_adjuntos}
    faltantes = [d for d in requeridos if norm(d) not in adjuntos]

    if faltantes:
        return {"decision": Decision.DOCUMENTOS_FALTANTES,
                "motivo": "Faltan documentos para pre-aprobar.",
                "faltantes": faltantes}

    return {"decision": Decision.PREAPROBADA,
            "motivo": "Cumple cobertura, carencia y documentación. Pre-aprobación emitida.",
            "faltantes": [],
            "autorizacion_id": f"AUT-{informe.paciente_id}-{hoy.strftime('%Y%m%d')}"}

# ---- Integración Notion: el esquema vive en preauth/esquema.py ----
from preauth.esquema import INFORMES_PROPS, POLIZAS_PROPS, RESOLUCIONES_PROPS
NOTION_SCHEMA = {
    "db_informes": sorted(INFORMES_PROPS),
    "db_polizas": sorted(POLIZAS_PROPS),
    "db_resoluciones": sorted(RESOLUCIONES_PROPS),
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
