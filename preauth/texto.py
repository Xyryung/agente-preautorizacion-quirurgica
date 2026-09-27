"""Utilidades de texto compartidas por reglas y extraccion."""
import re
import unicodedata

_ESPACIOS = re.compile(r"\s+")


def norm(texto: str | None) -> str:
    """Clave de comparacion: sin tildes, sin distinguir mayusculas, espacios simples.

    Solo sirve para COMPARAR. Nunca muestres su resultado al usuario:
    los mensajes usan el texto original o el nombre canonico.

    >>> norm("  COLECISTECTOMÍA ")
    'colecistectomia'
    """
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_marcas = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", sin_marcas).strip().casefold()