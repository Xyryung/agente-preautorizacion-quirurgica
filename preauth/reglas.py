"""
Agente de Pre-Autorización Quirúrgica en Tiempo Real
Flujo: Notion DB (Informe Hospital + Póliza) -> Agente IA -> Decisión instantánea
"""
import calendar
from dataclasses import dataclass, field, replace
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
    monto_reservado: float = 0  # suma de costos pre-aprobados aún no liquidados (issue #15)

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
    # Senales de la extraccion automatica (issue #6b). None/vacio: no hubo extraccion.
    confianza_extraccion: float | None = None
    citas_no_encontradas: List[str] = field(default_factory=list)
    # Coherencia diagnostico-procedimiento segun la IA (issue #7). None: no hubo extraccion.
    coherencia_diagnostico: str | None = None
    justificacion_coherencia: str = ""

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
    """Meses COMPLETOS de afiliacion al dia 'hoy'. Nunca es negativo.

    Un mes se cumple el mismo dia del mes siguiente. Si ese dia no existe
    (inicio el 31 y el mes tiene menos dias), se cumple el ultimo dia del mes.
    """
    inicio = poliza.fecha_inicio
    meses = (hoy.year - inicio.year) * 12 + (hoy.month - inicio.month)
    ultimo_dia_del_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    if hoy.day < inicio.day and hoy.day != ultimo_dia_del_mes:
        meses -= 1
    return max(meses, 0)

PROCEDIMIENTOS_SIN_DATO = {"", "desconocido"}
# Red de seguridad, no calibracion: en 10 informes sinteticos los casos normales
# dieron >= 0.90 y el informe vacio 0.60 (ver README).
UMBRAL_CONFIANZA = 0.7
MOTIVO_PREAPROBADA = "Cumple cobertura, carencia y documentación. Pre-aprobación emitida."

# Que hallazgos explican cada decision en el campo 'motivo'.
# Los INFORMATIVO se agregan siempre al final.
RESULTADOS_DEL_MOTIVO = {
    Decision.DENEGADA: {Resultado.NO_CUMPLE},
    Decision.REVISION_MANUAL: {Resultado.REVISION, Resultado.FALTAN_DOCUMENTOS},
    Decision.DOCUMENTOS_FALTANTES: {Resultado.FALTAN_DOCUMENTOS},
    Decision.PREAPROBADA: set(),
}


def _regla_datos(informe: InformeMedico) -> List[Hallazgo]:
    if norm(informe.procedimiento) in PROCEDIMIENTOS_SIN_DATO:
        return [Hallazgo("datos", Resultado.REVISION,
                         "El informe no indica un procedimiento reconocible; requiere revisión manual.")]
    return []

def _regla_extraccion(informe: InformeMedico) -> List[Hallazgo]:
    """Senales de la extraccion automatica. Como maximo un hallazgo (ver issue #6b)."""
    razones = []
    c = informe.confianza_extraccion
    if c is not None and c < UMBRAL_CONFIANZA:
        razones.append(f"confianza baja ({c:.2f} < {UMBRAL_CONFIANZA:.2f})")
    if informe.citas_no_encontradas:
        citas = "; ".join(f"'{x}'" for x in informe.citas_no_encontradas[:3])
        razones.append(f"citas que no aparecen en el informe: {citas}")
    if razones:
        return [Hallazgo("extraccion", Resultado.REVISION,
                         "La extracción automática requiere revisión manual: "
                         + "; ".join(razones) + ".")]
    return []

def _regla_coherencia(informe: InformeMedico) -> List[Hallazgo]:
    """Coherencia diagnostico-procedimiento segun la IA (issue #7).

    Solo "incoherente" genera un hallazgo, y siempre de REVISION: un juicio clinico
    del modelo puede enviar el caso a una persona, nunca denegarlo.
    """
    if norm(informe.coherencia_diagnostico or "") != "incoherente":
        return []
    justificacion = informe.justificacion_coherencia.strip() or "sin justificación"
    return [Hallazgo("coherencia", Resultado.REVISION,
                     f"El diagnóstico no parece justificar el procedimiento ({justificacion}); "
                     "requiere revisión manual.")]

def _poliza_vigente(poliza: Poliza, hoy: date) -> bool:
    return poliza.fecha_inicio <= hoy


def _regla_vigencia(poliza: Poliza, hoy: date) -> List[Hallazgo]:
    if not _poliza_vigente(poliza, hoy):
        return [Hallazgo("vigencia", Resultado.REVISION,
                         f"La póliza inicia el {poliza.fecha_inicio.isoformat()}, después de la "
                         f"fecha de evaluación ({hoy.isoformat()}); requiere revisión manual.")]
    return [Hallazgo("vigencia", Resultado.CUMPLE,
                     f"Póliza vigente desde {poliza.fecha_inicio.isoformat()}.")]

def _regla_cobertura(poliza: Poliza, informe: InformeMedico) -> List[Hallazgo]:
    if norm(informe.procedimiento) in PROCEDIMIENTOS_SIN_DATO:
        return []  # sin procedimiento no hay cobertura que evaluar (ver _regla_datos)
    if not _contiene(poliza.cobertura_procedimientos, informe.procedimiento):
        return [Hallazgo("cobertura", Resultado.NO_CUMPLE,
                         f"Procedimiento '{informe.procedimiento}' no cubierto por póliza.")]
    if _contiene(poliza.exclusiones, informe.procedimiento):
        return [Hallazgo("cobertura", Resultado.NO_CUMPLE, "Procedimiento en lista de exclusiones.")]
    return [Hallazgo("cobertura", Resultado.CUMPLE, "Procedimiento cubierto por la póliza.")]


def _regla_carencia(poliza: Poliza, informe: InformeMedico, hoy: date) -> List[Hallazgo]:
    if norm(informe.urgencia) == "emergencia":
        return [Hallazgo("carencia", Resultado.INFORMATIVO,
                         "Emergencia: se omitió la carencia; requiere revisión posterior.")]
    if not _poliza_vigente(poliza, hoy):
        return []  # sin vigencia no hay carencia que evaluar (ver _regla_vigencia)
    carencia_req = _buscar(poliza.carencia_meses, informe.procedimiento,
                           _buscar(poliza.carencia_meses, "default", 0))
    antiguedad = meses_afiliado(poliza, hoy)
    if antiguedad < carencia_req:
        return [Hallazgo("carencia", Resultado.NO_CUMPLE,
                         f"No cumple carencia: {antiguedad}/{carencia_req} meses.")]
    return [Hallazgo("carencia", Resultado.CUMPLE, f"Cumple carencia: {antiguedad}/{carencia_req} meses.")]


def _regla_monto(poliza: Poliza, informe: InformeMedico) -> List[Hallazgo]:
    if informe.costo_estimado <= 0:
        return [Hallazgo("monto", Resultado.FALTAN_DOCUMENTOS,
                         "Falta el costo estimado; se requiere el presupuesto del hospital.")]
    if poliza.monto_usado + poliza.monto_reservado + informe.costo_estimado > poliza.monto_maximo:
        return [Hallazgo("monto", Resultado.NO_CUMPLE, "Excede monto máximo de póliza.")]
    return [Hallazgo("monto", Resultado.CUMPLE, "Dentro del monto disponible de la póliza.")]


def _documentos_faltantes(poliza: Poliza, informe: InformeMedico) -> List[str]:
    requeridos = set(DOCS_BASE + _buscar(DOCS_POR_PROCEDIMIENTO, informe.procedimiento,
                                         DOCS_POR_PROCEDIMIENTO["default"]))
    if _contiene(poliza.requiere_segunda_opinion, informe.procedimiento):
        requeridos.add("segunda_opinion")
    if informe.costo_estimado <= 0:
        requeridos.add("presupuesto_hospital")
    adjuntos = {norm(d) for d in informe.documentos_adjuntos}
    return sorted(d for d in requeridos if norm(d) not in adjuntos)


def _regla_documentos(faltantes: List[str]) -> List[Hallazgo]:
    if faltantes:
        return [Hallazgo("documentos", Resultado.FALTAN_DOCUMENTOS, "Faltan documentos para pre-aprobar.")]
    return [Hallazgo("documentos", Resultado.CUMPLE, "Documentación completa.")]

def _motivo(decision: Decision, hallazgos: List[Hallazgo]) -> str:
    principales = [h.mensaje for h in hallazgos if h.resultado in RESULTADOS_DEL_MOTIVO[decision]]
    informativos = [h.mensaje for h in hallazgos if h.resultado is Resultado.INFORMATIVO]
    if decision is Decision.PREAPROBADA:
        # Sin informativos se conserva el texto historico exacto.
        principales = ["Pre-aprobación emitida."] if informativos else [MOTIVO_PREAPROBADA]
    return " ".join(principales + informativos)


def evaluar(poliza: Poliza, informe: InformeMedico, hoy: date | None = None) -> dict:
    """Evalua TODAS las reglas y decide por precedencia (ver decidir()).

    Devuelve un dict con las mismas claves de siempre (decision, motivo,
    faltantes y autorizacion_id si se pre-aprueba) mas 'hallazgos'.
    """
    if hoy is None:
        hoy = date.today()  # se lee en cada llamada, no una sola vez al importar

    faltantes = _documentos_faltantes(poliza, informe)
    hallazgos = (
        _regla_datos(informe)
        + _regla_extraccion(informe)
        + _regla_coherencia(informe)
        + _regla_vigencia(poliza, hoy)
        + _regla_cobertura(poliza, informe)
        + _regla_carencia(poliza, informe, hoy)
        + _regla_monto(poliza, informe)
        + _regla_documentos(faltantes)
    )
    decision = decidir(hallazgos)
    resultado = {
        "decision": decision,
        "motivo": _motivo(decision, hallazgos),
        # Una denegacion no se corrige con documentos: no se piden.
        "faltantes": [] if decision is Decision.DENEGADA else faltantes,
        "hallazgos": hallazgos,
    }
    if decision is Decision.PREAPROBADA:
        resultado["autorizacion_id"] = f"AUT-{informe.paciente_id}-{hoy.strftime('%Y%m%d')}"
        resultado["monto_reservado"] = poliza.monto_reservado + informe.costo_estimado
    return resultado


def reservar(poliza: Poliza, costo_estimado: float) -> Poliza:
    """Devuelve una póliza con el costo sumado al monto reservado (issue #15).

    No muta la póliza original: el llamador persiste el nuevo total
    (en Notion: `monto_usado` de la página de la póliza).
    """
    return replace(poliza, monto_reservado=poliza.monto_reservado + costo_estimado)

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
