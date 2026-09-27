"""Configuracion centralizada del proyecto.

- Carga .env una sola vez, al importar este modulo. No sobrescribe variables
  que ya existan en el entorno, asi que en Render ganan las del panel.
- Importar este modulo NUNCA falla por variables faltantes: cada grupo de
  variables se valida solo cuando alguien lo pide (validacion perezosa).
"""
import os
from dataclasses import dataclass
from functools import lru_cache

from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PROVEEDORES_LLM = {"openai", "ollama", "regex"}
ESFUERZOS_RAZONAMIENTO = {"minimal", "low", "medium", "high"}


class ConfigError(RuntimeError):
    """Falta una variable de entorno requerida o tiene un valor invalido."""


def _requerida(nombre: str) -> str:
    valor = os.getenv(nombre, "").strip()
    if not valor:
        raise ConfigError(
            f"Falta la variable de entorno {nombre}. "
            "Definela en tu .env (ver .env.example) o en el panel de Render."
        )
    return valor


def _opcional(nombre: str, por_defecto: str) -> str:
    return os.getenv(nombre, "").strip() or por_defecto


@dataclass(frozen=True)
class NotionSettings:
    token: str
    db_informes: str
    db_polizas: str
    db_resoluciones: str


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    openai_model: str
    openai_reasoning_effort: str
    ollama_url: str
    ollama_model: str


@lru_cache(maxsize=1)
def notion_settings() -> NotionSettings:
    """Variables de Notion. Lanza ConfigError si falta alguna."""
    return NotionSettings(
        token=_requerida("NOTION_TOKEN"),
        db_informes=_requerida("NOTION_DB_INFORMES"),
        db_polizas=_requerida("NOTION_DB_POLIZAS"),
        db_resoluciones=_requerida("NOTION_DB_RESOLUCIONES"),
    )


@lru_cache(maxsize=1)
def llm_settings() -> LLMSettings:
    """Variables del proveedor de IA. OPENAI_API_KEY la lee el SDK de OpenAI."""
    provider = _opcional("LLM_PROVIDER", "openai").lower()
    if provider not in PROVEEDORES_LLM:
        raise ConfigError(
            f"LLM_PROVIDER='{provider}' no es valido. Usa uno de: {sorted(PROVEEDORES_LLM)}."
        )
    esfuerzo = _opcional("OPENAI_REASONING_EFFORT", "minimal").lower()
    if esfuerzo not in ESFUERZOS_RAZONAMIENTO:
        raise ConfigError(
            f"OPENAI_REASONING_EFFORT='{esfuerzo}' no es valido. Usa uno de: {sorted(ESFUERZOS_RAZONAMIENTO)}."
        )
    return LLMSettings(
        provider=provider,
        openai_model=_opcional("OPENAI_MODEL", "gpt-5-mini"),
        openai_reasoning_effort=esfuerzo,
        ollama_url=_opcional("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=_opcional("OLLAMA_MODEL", "llama3.2:3b"),
    )

@lru_cache(maxsize=1)
def zona_horaria() -> ZoneInfo:
    """Zona horaria del negocio (ZONA_HORARIA, por defecto America/Panama). Issue #44."""
    raise NotImplementedError("issue #44")