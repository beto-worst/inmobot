"""Encuentra al asesor asignado a una propiedad.

Sin esto, el aviso de un prospecto cae en un buzon general y alguien tiene
que reenviarlo. Con esto le llega directo a quien tiene esa casa asignada,
que es quien la conoce y quien cobra la comision.

La fuente es una URL del propio sitio del cliente, configurada por inquilino.
El sitio de RedNorte expone /api/athena/asesor?code=XXX, que devuelve nombre,
telefono y correo del asesor de esa propiedad.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx

from .config import leer_env


class BuscadorAsesor:
    """Consulta el asesor de una propiedad, con cache.

    Config del inquilino:
        url        Plantilla con {codigo}, p. ej.
                   "https://www.rednorte.mx/api/athena/asesor?code={codigo}"
        token_env  Variable de entorno con el token, si el sitio lo pide.
        ttl        Segundos de cache (por defecto 6 horas). La asignacion de
                   asesor casi no cambia, y son 500+ propiedades: sin cache
                   se le pega al sitio del cliente en cada prospecto.
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.url = config.get("url", "")
        self.var_token = config.get("token_env", "")
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
        cabeceras = {"Accept": "application/json"}
        if self.var_token:
            token = leer_env(self.var_token)
            if not token:
                return None
            cabeceras["Authorization"] = f"Bearer {token}"

        try:
            url = self.url.format(codigo=codigo)
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(url, headers=cabeceras)
            if r.status_code != 200:
                return None
            datos = r.json() or {}
        except Exception:  # noqa: BLE001 - nunca tumbar la captura de un lead
            return None

        # Dos formatos: el del canal de Athena, en espanol, y el que usaban
        # los sitios viejos con {success, agent}. Se aceptan los dos para no
        # romper a un inquilino cuando su sitio todavia no se actualiza.
        if "asesor" in datos:
            agente = datos.get("asesor") or {}
            nombre = (agente.get("nombre") or "").strip()
            telefono = (agente.get("telefono") or "").strip()
            correo = (agente.get("correo") or "").strip()
            # El sitio avisa si cayo al contacto general en vez del asesor
            # de esa propiedad; eso no es un asesor asignado.
            if "propio" in datos and not datos.get("propio"):
                return None
        else:
            if not datos.get("success"):
                return None
            agente = datos.get("agent") or {}
            nombre = (agente.get("name") or "").strip()
            telefono = (agente.get("phone") or "").strip()
            correo = (agente.get("email") or "").strip()

        if not nombre:
            return None

        return {"nombre": nombre, "telefono": telefono, "correo": correo}
