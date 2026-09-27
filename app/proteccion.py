"""Protecciones de la demo publica contra abuso y costos (issue #11).

- Limite de solicitudes por IP (ventana deslizante de 60 s, en memoria).
- Limite de tamano del cuerpo de la solicitud y del texto del informe.
- Token de administrador para endpoints que escriben en Notion.
- Errores sin trazas ni secretos hacia el cliente.

Uso en app/main.py:
    from app import proteccion
    proteccion.instalar(app)

Uso en un endpoint que escribe en Notion:
    @app.post("/api/procesar-pendientes", dependencies=[Depends(proteccion.requiere_admin)])

Uso en un modelo de request:
    class SolicitudEvaluar(BaseModel):
        texto: proteccion.TextoInforme
"""
import hmac
import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

log = logging.getLogger("preauth.proteccion")

# Un informe medico real cabe de sobra; 50 000 caracteres se rechazan.
MAX_CARACTERES_TEXTO = 8000
MAX_BYTES_CUERPO = 32_000
VENTANA_SEGUNDOS = 60
# Solo se limita la API (lo que puede generar costos en OpenAI o escribir en
# Notion). La pagina, /docs, /health y el favicon no gastan cupo.
PREFIJO_LIMITADO = "/api/"

TextoInforme = Annotated[str, Field(min_length=1, max_length=MAX_CARACTERES_TEXTO)]


def limite_por_minuto() -> int:
    return int(os.getenv("RATE_LIMIT_POR_MINUTO", "10"))


class LimitadorPorIP:
    """Ventana deslizante por IP. Vive en memoria: se reinicia con cada deploy,
    suficiente para una sola instancia gratuita de Render."""

    def __init__(self):
        self._solicitudes: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, ip: str, limite: int, ahora: float | None = None) -> bool:
        ahora = time.monotonic() if ahora is None else ahora
        with self._lock:
            cola = self._solicitudes[ip]
            while cola and ahora - cola[0] >= VENTANA_SEGUNDOS:
                cola.popleft()
            if len(cola) >= limite:
                return False
            cola.append(ahora)
            return True

    def reiniciar(self):
        with self._lock:
            self._solicitudes.clear()


limitador = LimitadorPorIP()


def ip_cliente(request: Request) -> str:
    # Render sirve la app detras de Cloudflare, que siempre sobrescribe
    # CF-Connecting-IP con la IP real: el cliente no lo puede falsificar.
    # True-Client-IP no se usa: sin plan Enterprise podria venir del cliente.
    ip_cloudflare = request.headers.get("cf-connecting-ip")
    if ip_cloudflare:
        return ip_cloudflare.strip()
    # Fuera de Cloudflare (por ejemplo en local)
    reenviada = request.headers.get("x-forwarded-for")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def requiere_admin(authorization: str | None = Header(default=None)) -> None:
    """Dependencia para endpoints que escriben en Notion.
    Espera el encabezado: Authorization: Bearer <DEMO_ADMIN_TOKEN>."""
    esperado = os.getenv("DEMO_ADMIN_TOKEN", "")
    if not esperado:
        raise HTTPException(503, "Endpoint deshabilitado: falta configurar DEMO_ADMIN_TOKEN.")
    recibido = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(recibido.encode(), esperado.encode()):
        raise HTTPException(401, "Token de administrador invalido o ausente.")


def instalar(app: FastAPI) -> None:
    @app.middleware("http")
    async def limitar(request: Request, call_next):
        largo = request.headers.get("content-length")
        if largo and largo.isdigit() and int(largo) > MAX_BYTES_CUERPO:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Solicitud demasiado grande. El texto del informe admite "
                                   f"hasta {MAX_CARACTERES_TEXTO} caracteres."},
            )
        if request.url.path.startswith(PREFIJO_LIMITADO):
            if not limitador.permitir(ip_cliente(request), limite_por_minuto()):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Demasiadas solicitudes. Espera un minuto e intenta de nuevo."},
                    headers={"Retry-After": str(VENTANA_SEGUNDOS)},
                )
        return await call_next(request)

    @app.exception_handler(Exception)
    async def error_generico(request: Request, exc: Exception):
        # La traza completa queda solo en los logs de Render, nunca en la respuesta.
        log.exception("Error no controlado en %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})
