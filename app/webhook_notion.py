"""Webhook de Notion: dispara el agente cuando cambia la base de informes (issue #12).

Flujo:
1. Handshake (una sola vez): al crear la suscripcion, Notion envia
   {"verification_token": "..."} sin firma. El token se muestra en los logs
   de Render; se pega en Notion para verificar la suscripcion y se guarda en
   NOTION_WEBHOOK_VERIFICATION_TOKEN.
2. Eventos: cada solicitud trae X-Notion-Signature = "sha256=<hex>", un
   HMAC-SHA256 del cuerpo crudo con el verification_token como clave.
3. Se responde 200 de inmediato y el ciclo de Notion corre en segundo plano.

Por que se llama a run_once() y no se procesa solo la pagina del evento:
- Los eventos no traen los datos de la pagina y pueden llegar desordenados.
- run_once() solo toma informes con estado "pendiente", asi que nuestras propias
  escrituras (la resolucion creada y el cambio de estado) no provocan bucles.
"""
import hashlib
import hmac
import json
import logging
import os
import threading
from collections import OrderedDict

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

log = logging.getLogger("preauth.webhook")

router = APIRouter()

# Eventos que pueden significar "hay un informe nuevo o actualizado".
TIPOS_RELEVANTES = {"page.created", "page.properties_updated", "page.content_updated"}
MAX_EVENTOS_RECORDADOS = 1000


def token_verificacion() -> str:
    return os.getenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", "").strip()


def firma_valida(cuerpo: bytes, firma: str | None, token: str) -> bool:
    if not firma or not token:
        return False
    esperada = "sha256=" + hmac.new(token.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma.strip())


class EventosVistos:
    """Recuerda los ultimos ids de evento: Notion reintenta hasta 8 veces."""

    def __init__(self, maximo: int = MAX_EVENTOS_RECORDADOS):
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._maximo = maximo
        self._lock = threading.Lock()

    def es_nuevo(self, evento_id: str) -> bool:
        with self._lock:
            if evento_id in self._ids:
                return False
            self._ids[evento_id] = None
            if len(self._ids) > self._maximo:
                self._ids.popitem(last=False)
            return True

    def reiniciar(self):
        with self._lock:
            self._ids.clear()


eventos_vistos = EventosVistos()


class Procesador:
    """Ejecuta un solo ciclo de Notion a la vez.

    Si llega un evento mientras un ciclo corre, no se lanza otro en paralelo
    (duplicaria resoluciones): se marca y se repite una vez al terminar.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._corriendo = False
        self._repetir = False

    def ejecutar(self, ciclo=None):
        if ciclo is None:
            # Import diferido: el servidor arranca aunque falten las variables de Notion.
            from preauth.notion_repo import run_once as ciclo
        with self._lock:
            if self._corriendo:
                self._repetir = True
                return
            self._corriendo = True
        terminado = False
        try:
            while True:
                try:
                    ciclo()
                except Exception:
                    log.exception("Fallo el ciclo de Notion disparado por webhook")
                with self._lock:
                    # Se decide y se libera en el mismo bloqueo para no perder
                    # un evento que llegue justo ahora.
                    if not self._repetir:
                        self._corriendo = False
                        terminado = True
                        return
                    self._repetir = False
        finally:
            if not terminado:
                with self._lock:
                    self._corriendo = False


procesador = Procesador()


@router.post("/webhook/notion")
async def webhook_notion(request: Request, tareas: BackgroundTasks) -> dict:
    cuerpo = await request.body()
    try:
        datos = json.loads(cuerpo)
    except ValueError:
        raise HTTPException(400, "Cuerpo JSON invalido.")
    if not isinstance(datos, dict):
        raise HTTPException(400, "Cuerpo JSON invalido.")

    token = token_verificacion()

    # 1. Handshake: llega sin firma y solo trae el verification_token.
    if "verification_token" in datos and "type" not in datos:
        if token:
            # Ya hay una suscripcion verificada: no se aceptan handshakes nuevos.
            return {"status": "ignorado"}
        log.warning(
            "Handshake de Notion recibido. Pega este token en Notion para verificar "
            "la suscripcion y guardalo en NOTION_WEBHOOK_VERIFICATION_TOKEN: %s",
            datos["verification_token"],
        )
        return {"status": "handshake recibido"}

    # 2. Eventos: siempre firmados.
    if not token:
        raise HTTPException(503, "Webhook deshabilitado: falta NOTION_WEBHOOK_VERIFICATION_TOKEN.")
    if not firma_valida(cuerpo, request.headers.get("x-notion-signature"), token):
        raise HTTPException(401, "Firma de Notion invalida.")

    evento_id = str(datos.get("id", ""))
    if evento_id and not eventos_vistos.es_nuevo(evento_id):
        return {"status": "duplicado"}
    entidad = datos.get("entity") or {}
    if datos.get("type") not in TIPOS_RELEVANTES or entidad.get("type") != "page":
        return {"status": "ignorado"}

    tareas.add_task(procesador.ejecutar)
    return {"status": "aceptado"}
