"""Encuentra al asesor asignado a una propiedad.

Sin esto, el aviso de un prospecto cae en un buzon general y alguien tiene
que reenviarlo. Con esto le llega directo a quien tiene esa casa asignada,
que es quien la conoce y quien cobra la comision.

La fuente es una URL del propio sitio del cliente, configurada por inquilino.
El sitio de RedNorte ya expone /api/asesor?code=XXX, que devuelve nombre,
telefono y correo del asesor de esa propiedad.
"""
from __future__ import annotations

import asyncio
import time

import httpx


class BuscadorAsesor:
    """Consulta el asesor de una propiedad, con cache.

    Config del inquilino:
        url   Plantilla con {codigo}, p. ej.
              "https://www.rednorte.mx/api/asesor?code={codigo}"
        ttl   Segundos de cache (por defecto 6 horas). La asignacion de
              asesor casi no cambia, y son 500+ propiedades: sin cache se
              le pega al sitio del cliente en cada prospecto.
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.url = config.get("url", "")
        self.ttl = int(config.get("ttl", 6 * 3600))
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self._candado = asyncio.Lock()

    @property
    def activo(self) -> bool:
        return bool(self.url)

    async def buscar(self, codigo: str) -> dict | None:
        """Devuelve {nombre, telefono, correo} o None si no se pudo."""
        codigo = str(codigo or "").strip()
        if not codigo or not self.activo:
            return None

        async with self._candado:
            guardado = self._cache.get(codigo.upper())
            if guardado and (time.time() - guardado[0]) < self.ttl:
                return guardado[1]

        asesor = await self._consultar(codigo)

        async with self._candado:
            self._cache[codigo.upper()] = (time.time(), asesor)
        return asesor

    async def _consultar(self, codigo: str) -> dict | None:
        try:
            url = self.url.format(codigo=codigo)
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(url)
            if r.status_code != 200:
                return None
            datos = r.json() or {}
        except Exception:  # noqa: BLE001 - nunca tumbar la captura de un lead
            return None

        if not datos.get("success"):
            return None

        agente = datos.get("agent") or {}
        nombre = (agente.get("name") or "").strip()
        if not nombre:
            return None

        return {
            "nombre": nombre,
            "telefono": (agente.get("phone") or "").strip(),
            "correo": (agente.get("email") or "").strip(),
        }
