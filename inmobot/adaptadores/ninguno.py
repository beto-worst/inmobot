"""Para negocios que no tienen catalogo que consultar.

No todo cliente vende propiedades. Un despacho, una aseguradora o nosotros
mismos vendiendo Athena: lo que el asistente necesita saber cabe en su
configuracion y no cambia cada hora, asi que no hay inventario que bajar
ni herramienta de busqueda que ofrecer.

Con este adaptador el asistente no recibe las herramientas de propiedades:
no puede buscar porque no hay donde, y tampoco puede inventar que busco.
Lo que sabe es lo que diga su campo 'conocimiento', y nada mas.
"""
from __future__ import annotations

from ..modelos import Propiedad
from .base import AdaptadorInventario


class AdaptadorNinguno(AdaptadorInventario):
    tipo = "ninguno"

    async def _descargar(self) -> list[Propiedad]:
        return []
