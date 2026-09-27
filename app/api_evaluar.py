"""API de evaluacion para la demo publica (issue #9).

POST /api/evaluar
    Poliza: `poliza_id` (una de las polizas de demo) o `poliza` completa.
    Informe: `texto` libre (la IA extrae los datos) o `informe` estructurado.
    Devuelve la decision, los hallazgos de cada regla, los faltantes, lo que
    extrajo la IA y la latencia.

POST /api/procesar-pendientes
    Ejecuta el ciclo de Notion en segundo plano. Requiere DEMO_ADMIN_TOKEN.

La IA solo extrae; las reglas deciden. Probar la API no escribe en Notion.
"""
import logging
import time
from dataclasses import asdict
from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import proteccion, webhook_notion
from app.casos_demo import POLIZAS_DEMO
from preauth.extraccion import NO_INDICADA, ExtraccionInforme, ResultadoExtraccion, extraer
from preauth.reglas import InformeMedico, Poliza, evaluar

log = logging.getLogger("preauth.api")

router = APIRouter(prefix="/api", tags=["evaluación"])

MAX_ELEMENTOS = 50  # listas de la poliza o del informe
MONTO_MAXIMO_RAZONABLE = 10_000_000  # tope de cordura para montos de la demo
CARENCIA_MAXIMA_MESES = 120  # 10 años: nadie tiene carencias mas largas
FECHA_MINIMA = date(1900, 1, 1)


# --------------------------------------------------------------------------
# Modelos de entrada
# --------------------------------------------------------------------------
class PolizaEntrada(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)  # NaN/Infinity -> 422, no 500

    paciente_id: str = Field(max_length=50, examples=["P001"])
    cobertura: list[str] = Field(max_length=MAX_ELEMENTOS, examples=[["Colecistectomía"]])
    fecha_inicio: date = Field(examples=["2024-01-01"])
    carencia_meses: dict[str, int] = Field(default={"default": 0}, examples=[{"default": 8}])
    monto_maximo: float = Field(gt=0, le=MONTO_MAXIMO_RAZONABLE, examples=[50000])
    monto_usado: float = Field(default=0, ge=0, le=MONTO_MAXIMO_RAZONABLE, examples=[5000])
    exclusiones: list[str] = Field(default=[], max_length=MAX_ELEMENTOS)
    requiere_segunda_opinion: list[str] = Field(default=[], max_length=MAX_ELEMENTOS)

    @field_validator("fecha_inicio")
    @classmethod
    def fecha_a_partir_de_1900(cls, v: date) -> date:
        if v < FECHA_MINIMA:
            raise ValueError("La fecha de inicio debe ser a partir de 1900.")
        return v

    @field_validator("carencia_meses")
    @classmethod
    def carencia_entre_0_y_120(cls, v: dict[str, int]) -> dict[str, int]:
        for proc, meses in v.items():
            if not 0 <= meses <= CARENCIA_MAXIMA_MESES:
                raise ValueError(
                    f"La carencia de '{proc}' debe estar entre 0 y {CARENCIA_MAXIMA_MESES} meses.")
        return v

    def a_poliza(self) -> Poliza:
        return Poliza(self.paciente_id, self.cobertura, self.fecha_inicio, self.carencia_meses,
                      self.monto_maximo, self.monto_usado, self.exclusiones,
                      self.requiere_segunda_opinion)


class InformeEntrada(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)  # NaN/Infinity -> 422, no 500

    procedimiento: str = Field(max_length=100, examples=["Colecistectomía"])
    diagnostico_cie10: str = Field(default="", max_length=20, examples=["K80.2"])
    medico: str = Field(default="", max_length=100)
    urgencia: Literal["programada", "emergencia"] = "programada"
    documentos_adjuntos: list[str] = Field(default=[], max_length=MAX_ELEMENTOS)
    costo_estimado: float = Field(default=0, ge=0, le=MONTO_MAXIMO_RAZONABLE, examples=[8000])


class SolicitudEvaluar(BaseModel):
    poliza_id: str | None = Field(default=None, examples=["P001"],
                                  description="Una de las pólizas de demo: P001, P002, P003 o P004.")
    poliza: PolizaEntrada | None = None
    texto: proteccion.TextoInforme | None = Field(
        default=None, description="Informe médico en texto libre (máximo 8000 caracteres).",
        examples=["Paciente P001. Colelitiasis (K80.2). Colecistectomía programada. Se adjuntan cédula, "
                  "informe médico, consentimiento, ecografía abdominal y analítica. Presupuesto: 8000."])
    informe: InformeEntrada | None = None

    @model_validator(mode="after")
    def una_fuente_de_cada_cosa(self):
        if (self.poliza_id is None) == (self.poliza is None):
            raise ValueError("Envía la póliza como 'poliza_id' o como 'poliza', pero no ambas.")
        if (self.texto is None) == (self.informe is None):
            raise ValueError("Envía el informe como 'texto' o como 'informe', pero no ambos.")
        return self


# --------------------------------------------------------------------------
# Modelos de salida
# --------------------------------------------------------------------------
class HallazgoSalida(BaseModel):
    regla: str
    resultado: str
    mensaje: str


class ExtraccionSalida(BaseModel):
    proveedor: str = Field(description="'openai' o 'regex' si se usó el respaldo.")
    respaldo: bool = Field(description="True si la IA no respondió y se usó el extractor de respaldo.")
    confianza: float
    datos: ExtraccionInforme
    advertencias: list[str]


class Latencia(BaseModel):
    extraccion_ms: float
    reglas_ms: float
    total_ms: float


class RespuestaEvaluar(BaseModel):
    decision: str
    motivo: str
    faltantes: list[str]
    hallazgos: list[HallazgoSalida]
    autorizacion_id: str | None = None
    poliza_id: str
    extraccion: ExtraccionSalida | None = None
    latencia: Latencia


# --------------------------------------------------------------------------
# Logica
# --------------------------------------------------------------------------
def obtener_extractor():
    """Dependencia: las pruebas la sustituyen con app.dependency_overrides."""
    return extraer


def informe_desde_extraccion(r: ResultadoExtraccion, paciente_id: str) -> InformeMedico:
    """Contrato de #6: convierte lo que extrae la IA en la entrada de las reglas.

    Incluye las senales de la extraccion (6b y #7): con confianza baja, citas que
    no estan en el informe o un diagnostico incoherente, las reglas mandan el caso
    a revision manual (#40).
    """
    datos = r.datos
    return InformeMedico(
        paciente_id=paciente_id,
        procedimiento=datos.procedimiento,  # "desconocido" va a revision manual
        diagnostico_cie10=datos.cie10 or "",
        medico=datos.medico or "",
        urgencia="programada" if datos.urgencia == NO_INDICADA else datos.urgencia,
        documentos_adjuntos=list(datos.documentos_aportados),
        costo_estimado=datos.costo_estimado or 0,  # sin costo, la regla de monto pide presupuesto
        confianza_extraccion=datos.confianza,
        citas_no_encontradas=list(r.citas_no_encontradas),
        coherencia_diagnostico=datos.coherencia_diagnostico,
        justificacion_coherencia=datos.justificacion_coherencia,
        procedimiento_regex=r.procedimiento_regex,
    )


def resolver_poliza(solicitud: SolicitudEvaluar) -> tuple[str, Poliza]:
    if solicitud.poliza is not None:
        return solicitud.poliza.paciente_id, solicitud.poliza.a_poliza()
    demo = POLIZAS_DEMO.get(solicitud.poliza_id.strip().upper())
    if demo is None:
        raise HTTPException(404, f"Póliza '{solicitud.poliza_id}' no existe. "
                                 f"Usa una de: {', '.join(POLIZAS_DEMO)}.")
    return demo.poliza.paciente_id, demo.poliza


@router.post("/evaluar", response_model=RespuestaEvaluar,
             summary="Evalúa un informe médico contra una póliza")
def api_evaluar(solicitud: SolicitudEvaluar, extractor=Depends(obtener_extractor)) -> RespuestaEvaluar:
    inicio = time.perf_counter()
    poliza_id, poliza = resolver_poliza(solicitud)

    extraccion = None
    if solicitud.texto is not None:
        r = extractor(solicitud.texto)
        if r.error:
            # El detalle queda en los logs; al cliente no se le devuelven errores internos.
            log.warning("La extraccion uso el respaldo: %s", r.error)
        extraccion = ExtraccionSalida(
            proveedor=r.proveedor, respaldo=r.error is not None, confianza=r.datos.confianza,
            datos=r.datos, advertencias=list(r.advertencias),
        )
        informe = informe_desde_extraccion(r, poliza_id)
    else:
        informe = InformeMedico(paciente_id=poliza_id, **solicitud.informe.model_dump())
    fin_extraccion = time.perf_counter()

    resultado = evaluar(poliza, informe)
    fin = time.perf_counter()

    return RespuestaEvaluar(
        decision=resultado["decision"].value,
        motivo=resultado["motivo"],
        faltantes=list(resultado["faltantes"]),
        hallazgos=[HallazgoSalida(**{**asdict(h), "resultado": h.resultado.value})
                   for h in resultado["hallazgos"]],
        autorizacion_id=resultado.get("autorizacion_id"),
        poliza_id=poliza_id,
        extraccion=extraccion,
        latencia=Latencia(
            extraccion_ms=round((fin_extraccion - inicio) * 1000, 1),
            reglas_ms=round((fin - fin_extraccion) * 1000, 3),
            total_ms=round((fin - inicio) * 1000, 1),
        ),
    )


@router.post("/procesar-pendientes", status_code=202,
             dependencies=[Depends(proteccion.requiere_admin)],
             summary="Procesa los informes pendientes de Notion (requiere token de administrador)")
def api_procesar_pendientes(tareas: BackgroundTasks) -> dict:
    # Mismo procesador que el webhook: nunca corren dos ciclos de Notion a la vez.
    tareas.add_task(webhook_notion.procesador.ejecutar)
    return {"status": "aceptado"}
