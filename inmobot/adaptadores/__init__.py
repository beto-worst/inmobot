"""Registro de adaptadores disponibles.

Para dar de alta una inmobiliaria con una fuente de datos nueva: escribe la
clase, importala aqui y agregala al diccionario. Nada mas del sistema cambia.
"""
from .archivo import AdaptadorArchivo
from .base import AdaptadorInventario
from .demo import AdaptadorDemo
from .ninguno import AdaptadorNinguno
from .nocnok import AdaptadorNocnok
from .sitio import AdaptadorSitio

ADAPTADORES = {
    "demo": AdaptadorDemo,
    "nocnok": AdaptadorNocnok,
    "archivo": AdaptadorArchivo,
    "sitio": AdaptadorSitio,
    "ninguno": AdaptadorNinguno,
}


def construir(tipo: str, config: dict | None = None) -> AdaptadorInventario:
    if tipo not in ADAPTADORES:
        disponibles = ", ".join(sorted(ADAPTADORES))
        raise ValueError(
            f"Adaptador '{tipo}' no existe. Disponibles: {disponibles}"
        )
    return ADAPTADORES[tipo](config or {})


__all__ = ["AdaptadorInventario", "ADAPTADORES", "construir"]
