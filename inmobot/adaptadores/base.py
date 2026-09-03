"""Adaptador base: la unica pieza que se escribe por cada inmobiliaria nueva.

Una inmobiliaria nueva implementa UN metodo, `_descargar()`, que devuelve su
inventario como lista de Propiedad. Todo lo demas -filtrado, relajacion de
criterios, cache, ordenamiento- lo hereda gratis de aqui.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import replace

from ..modelos import Filtros, Propiedad, normalizar


class AdaptadorInventario(ABC):
    """Fuente de propiedades de un inquilino."""

    tipo = "base"

    def __init__(self, config: dict | None = None, ttl_segundos: int = 300):
        self.config = config or {}
        self.ttl = ttl_segundos
        self._cache: list[Propiedad] = []
        self._cargado_en: float = 0.0

    # ---- lo unico que implementa cada inmobiliaria ----
    @abstractmethod
    async def _descargar(self) -> list[Propiedad]:
        """Trae el inventario completo desde la fuente real."""

    # ---- de aqui para abajo es gratis para todos los inquilinos ----
    async def cargar(self, forzar: bool = False) -> list[Propiedad]:
        """Inventario con cache. Evita pegarle al CRM en cada mensaje del chat."""
        fresco = (time.time() - self._cargado_en) < self.ttl
        if self._cache and fresco and not forzar:
            return self._cache
        self._cache = await self._descargar()
        self._cargado_en = time.time()
        return self._cache

    async def detalle(self, id_propiedad: str) -> Propiedad | None:
        objetivo = normalizar(id_propiedad)
        for p in await self.cargar():
            if normalizar(p.id) == objetivo:
                return p
        return None

    async def buscar(self, f: Filtros) -> dict:
        """Busca con relajacion progresiva.

        Regla de producto: nunca devolver una lista vacia en seco. Un cliente
        que pide algo que no existe merece la opcion mas cercana MAS el aviso
        explicito de que se aflojo un criterio; asi el asesor sabe con que
        llega y el cliente no se va del sitio.
        """
        inventario = await self.cargar()
        if not inventario:
            return {"resultados": [], "total_coincidencias": 0,
                    "busqueda": f.descripcion_humana(),
                    "nota": "El inventario no esta disponible en este momento."}

        for filtros, aviso in self._escalera(f):
            hits = [p for p in inventario if self._coincide(p, filtros)]
            if hits:
                hits.sort(key=lambda p: self._relevancia(p, filtros))
                return {
                    "resultados": [p.resumen() for p in hits[: f.limite]],
                    "total_coincidencias": len(hits),
                    "busqueda": f.descripcion_humana(),
                    "nota": aviso,
                }

        return {"resultados": [], "total_coincidencias": 0,
                "busqueda": f.descripcion_humana(),
                "nota": "No hay nada en el inventario ni aflojando los criterios."}

    def _escalera(self, f: Filtros):
        """Criterios de mas estricto a mas flojo, con el aviso de que se cedio."""
        yield f, None

        if f.precio_max:
            yield (replace(f, precio_max=f.precio_max * 1.15),
                   f"No habia nada dentro de ${f.precio_max:,.0f}. Estas opciones "
                   f"se pasan hasta un 15% del presupuesto; dilo claramente.")

        if f.colonia:
            yield (replace(f, colonia=None),
                   f"No hay inventario en {f.colonia}. Estas son de la misma "
                   f"zona/municipio; avisale al cliente que cambio la colonia.")

        if f.recamaras_min and f.recamaras_min > 1:
            yield (replace(f, recamaras_min=f.recamaras_min - 1, colonia=None),
                   f"No habia con {f.recamaras_min} recamaras. Estas tienen una "
                   f"menos; dilo antes de presentarlas.")

        if f.municipio or f.colonia:
            yield (replace(f, municipio=None, colonia=None),
                   "No hay nada en esa zona. Estas son de otras zonas de "
                   "operacion; aclaralo de entrada.")

        if f.precio_max or f.recamaras_min:
            yield (replace(f, precio_max=None, recamaras_min=None,
                           colonia=None, municipio=None),
                   "Ninguna opcion cumple lo pedido. Esto es lo mas cercano "
                   "que hay; se honesto con el cliente sobre la diferencia.")

    def _coincide(self, p: Propiedad, f: Filtros) -> bool:
        if f.operacion and normalizar(p.operacion) != normalizar(f.operacion):
            return False
        if f.tipo and normalizar(p.tipo) not in {normalizar(t) for t in f.tipo}:
            return False
        if f.municipio and normalizar(f.municipio) not in normalizar(p.municipio):
            return False
        if f.colonia and normalizar(f.colonia) not in normalizar(p.colonia):
            return False
        if f.recamaras_min and p.recamaras < f.recamaras_min:
            return False
        if f.banos_min and p.banos < f.banos_min:
            return False
        if f.estacionamientos_min and p.estacionamientos < f.estacionamientos_min:
            return False
        # Precio 0 = "a consultar": nunca lo descartamos por presupuesto,
        # pero tampoco lo colamos como si cupiera. Se deja pasar y el agente
        # lo presenta como precio a consultar.
        if p.precio > 0:
            if f.precio_min and p.precio < f.precio_min:
                return False
            if f.precio_max and p.precio > f.precio_max:
                return False
        if f.construccion_min and p.construccion_m2 < f.construccion_min:
            return False
        if f.terreno_min and p.terreno_m2 < f.terreno_min:
            return False
        if f.texto:
            palabras = [w for w in normalizar(f.texto).split() if len(w) > 2]
            cuerpo = p.texto_busqueda
            if palabras and not any(w in cuerpo for w in palabras):
                return False
        return True

    def _relevancia(self, p: Propiedad, f: Filtros) -> tuple:
        """Menor es mejor. Primero lo que calza exacto, luego lo mas barato."""
        castigo = 0
        if f.recamaras_min and p.recamaras > f.recamaras_min + 1:
            castigo += 1  # de mas tambien es desajuste: sube precio y mantenimiento
        if not p.imagenes:
            castigo += 2  # sin fotos no se vende; va hasta abajo
        return (castigo, p.precio if p.precio > 0 else float("inf"))
