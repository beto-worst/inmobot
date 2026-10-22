"""Adaptador 'sitio': el propio sitio del cliente entrega su inventario.

Cuando la inmobiliaria ya tiene un sitio que baja su CRM -y casi todas lo
tienen-, leerlo de ahi es mejor que pegarle al CRM por nuestra cuenta:

  - El sitio ya paga esa llamada y guarda el resultado en cache. Nosotros
    repartimos una copia, no agregamos carga.
  - La llave del CRM se queda del lado del cliente. Nunca viaja, nunca vive
    en nuestro servidor, y no hay que pedirsela para cerrar la venta.
  - Si el sitio y el asistente leen la misma fuente, no pueden contradecirse.

Contrato que debe cumplir el sitio:

    GET <url>
    Authorization: Bearer <token>

    {
      "fuente": "live",            # o "fallback" si su CRM no respondio
      "total": 548,
      "propiedades": [
        {"id": "RN-123", "title": "...", "type": "Casa",
         "category": "Residencial", "op": "Venta",
         "colonia": "Cumbres", "municipio": "Monterrey",
         "zone": "Cumbres, Monterrey, Nuevo Leon",
         "price": "$2,850,000", "rawPrice": 2850000, "currency": "MXN",
         "rooms": 3, "baths": 2.5, "parking": 2,
         "constructionSize": 180, "lotSize": 200,
         "description": "...", "features": ["Alberca"],
         "img": "https://...", "url": "https://..."}
      ]
    }

Los nombres en ingles no son capricho: son los que ya usa el sitio de
RedNorte internamente. Pedirle al cliente que renombre sus campos para
nosotros es trabajo suyo que no hace falta; traducir es trabajo nuestro y
va aqui, en una sola funcion.
"""
from __future__ import annotations

import os

import httpx

from ..config import leer_env
from ..modelos import Propiedad
from .base import AdaptadorInventario
from .nocnok import ErrorInventario


def _numero(valor) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _entero(valor) -> int:
    return int(_numero(valor))


class AdaptadorSitio(AdaptadorInventario):
    tipo = "sitio"

    def __init__(self, config: dict | None = None, ttl_segundos: int = 300):
        super().__init__(config, ttl_segundos)
        self.url = self.config.get("url", "")
        self.var_token = self.config.get("token_env", "ATHENA_TOKEN")
        # Por si el sitio no arma el link publico de cada ficha.
        self.url_ficha = self.config.get("url_ficha", "")
        # Un sitio puede caer a su inventario de respaldo -cuatro o cinco
        # propiedades de muestra- cuando su CRM no contesta. Presentarlas
        # como si fueran el catalogo completo seria mentirle al cliente.
        self.aceptar_respaldo = bool(self.config.get("aceptar_respaldo", False))

    @property
    def token(self) -> str:
        valor = leer_env(self.var_token)
        if not valor:
            raise ErrorInventario(
                f"Falta la variable de entorno {self.var_token}. "
                f"Debe ser el mismo token que el sitio tiene configurado."
            )
        return valor

    async def _descargar(self) -> list[Propiedad]:
        if not self.url:
            raise ErrorInventario("El inquilino no tiene 'url' en su adaptador.")

        cabeceras = {"Authorization": f"Bearer {self.token}",
                     "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as c:
            r = await c.get(self.url, headers=cabeceras)

        if r.status_code == 401:
            raise ErrorInventario(
                f"El sitio rechazo el token de {self.var_token}. "
                f"Tiene que ser identico al que trae configurado el sitio.")
        if r.status_code == 503:
            raise ErrorInventario(
                "El sitio todavia no tiene encendido el canal del asistente.")
        if r.status_code != 200:
            raise ErrorInventario(
                f"El sitio respondio {r.status_code}: {r.text[:200]}")

        datos = r.json() or {}
        crudas = datos.get("propiedades") or []
        if not crudas:
            raise ErrorInventario("El sitio respondio sin propiedades.")

        if datos.get("fuente") == "fallback" and not self.aceptar_respaldo:
            raise ErrorInventario(
                f"El sitio esta sirviendo su inventario de respaldo "
                f"({len(crudas)} propiedades), no el real. Su CRM no esta "
                f"respondiendo.")

        return [self._mapear(p) for p in crudas]

    def _mapear(self, p: dict) -> Propiedad:
        codigo = str(p.get("id") or "")

        # El estado viene dentro de 'zone' ("Colonia, Municipio, Estado"),
        # que es como lo arma el sitio para mostrarlo de corrido.
        municipio = (p.get("municipio") or "").strip()
        partes = [t.strip() for t in str(p.get("zone") or "").split(",") if t.strip()]
        estado = partes[-1] if len(partes) >= 2 and partes[-1] != municipio else ""

        url = p.get("url") or ""
        if not url and self.url_ficha and codigo:
            url = self.url_ficha.format(codigo=codigo.lower())

        imagen = p.get("img") or ""

        return Propiedad(
            id=codigo,
            titulo=p.get("title") or "Propiedad",
            tipo=p.get("type") or "Casa",
            categoria=p.get("category") or "Residencial",
            operacion=p.get("op") or "Venta",
            precio=_numero(p.get("rawPrice")),
            precio_texto=p.get("price") or "Precio a consultar",
            moneda=p.get("currency") or "MXN",
            colonia=(p.get("colonia") or "").strip(),
            municipio=municipio,
            estado=estado,
            recamaras=_entero(p.get("rooms")),
            banos=_numero(p.get("baths")),
            estacionamientos=_entero(p.get("parking")),
            construccion_m2=_numero(p.get("constructionSize")),
            terreno_m2=_numero(p.get("lotSize")),
            descripcion=p.get("description") or "",
            amenidades=[str(a) for a in (p.get("features") or []) if a],
            imagenes=[imagen] if imagen else [],
            url=url,
        )
