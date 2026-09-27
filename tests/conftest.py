import pytest

from app import proteccion


@pytest.fixture(autouse=True)
def limitador_limpio():
    """Cada prueba empieza sin solicitudes contadas por el limite por IP."""
    proteccion.limitador.reiniciar()
    yield
    proteccion.limitador.reiniciar()
