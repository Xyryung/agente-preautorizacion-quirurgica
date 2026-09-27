import pytest

from app import proteccion
from preauth import config

@pytest.fixture(autouse=True)
def limitador_limpio():
    """Cada prueba empieza sin solicitudes contadas por el limite por IP."""
    proteccion.limitador.reiniciar()
    yield
    proteccion.limitador.reiniciar()

@pytest.fixture(autouse=True)
def extraccion_sin_red(monkeypatch):
    """Ninguna prueba llama a OpenAI por accidente: el proveedor por defecto es regex.

    Las pruebas del proveedor openai inyectan un cliente falso (ver test_extraccion_ia.py).
    """
    monkeypatch.setenv("LLM_PROVIDER", "regex")
    config.llm_settings.cache_clear()
    yield
    config.llm_settings.cache_clear()