"""Guarda en Notion el informe evaluado desde la web (issue #34).

POST /api/guardar-informe
    Crea una fila en Informes_Hospital con `informe_texto` y `estado=pendiente`.
    El worker la recoge en el siguiente ciclo (polling o webhook) y la procesa.
    Requiere DEMO_ADMIN_TOKEN (como /api/procesar-pendientes).

La IA solo extrae; las reglas deciden. Evaluar no escribe en Notion;
guardar sí, por eso pide token de administrador.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import proteccion

log = logging.getLogger("preauth.api")

router = APIRouter(prefix="/api", tags=["evaluación"])


class SolicitudGuardar(BaseModel):
    texto: proteccion.TextoInforme


def _crear_informe_pendiente(texto: str) -> str:
    # Import diferido: el servidor arranca aunque falten las variables de Notion.
    from preauth import esquema as E
    from preauth.notion_repo import con_reintentos, data_sources_ids, get_client
    notion = get_client()
    ds = data_sources_ids()["informes"]
    pagina = con_reintentos(notion.pages.create, parent={"data_source_id": ds}, properties={
        E.INF_TEXTO: {"rich_text": [{"text": {"content": texto}}]},
        E.INF_ESTADO: {"status": {"name": E.ESTADO_PENDIENTE}},
    })
    return pagina["id"]


@router.post("/guardar-informe",
             dependencies=[Depends(proteccion.requiere_admin)],
             summary="Guarda el informe en Notion como pendiente (requiere token de administrador)")
def api_guardar_informe(solicitud: SolicitudGuardar) -> dict:
    page_id = _crear_informe_pendiente(solicitud.texto)
    log.info("Informe guardado en Notion como pendiente: %s", page_id)
    return {"status": "guardado", "page_id": page_id, "estado": "pendiente"}
