"""Adaptador para inmobiliarias que traen su inventario en NOCNOK.

Sirve para RedNorte y para cualquier otro cliente que use el mismo CRM: solo
cambia la llave de API en su archivo de inquilino. El mapeo de campos replica
el que ya usa el sitio de RedNorte en `mapNocnokProperty()`, para que el chat
y el sitio nunca muestren datos distintos de la misma propiedad.
"""
from __future__ import annotations

import os

import httpx

from ..modelos import Propiedad
from .base import AdaptadorInventario

URL_BUSQUEDA = "https://api.nocnok.com/v1/sites/properties/search"

TIPO = {
    "House": "Casa", "Apartment": "Depto", "Land": "Terreno",
    "Ranch": "Casa", "CountryHouse": "Casa", "Warehouse": "Bodega",
    "Shop": "Local", "MedicalOffice": "Oficina", "Office": "Oficina",
    "Building": "Depto", "Hotel": "Local", "IndustrialWarehouse": "Bodega",
}

CATEGORIA = {
    "Habitational": "Residencial",
    "Commercial": "Comercial",
    "Industrial": "Industrial",
}


class ErrorInventario(RuntimeError):
    """La fuente de inventario no respondio o respondio mal."""


class AdaptadorNocnok(AdaptadorInventario):
    tipo = "nocnok"

    def __init__(self, config: dict | None = None, ttl_segundos: int = 300):
        super().__init__(config, ttl_segundos)
        self.var_llave = self.config.get("api_key_env", "NOCNOK_API_KEY")
        # NOCNOK topa el tamano de pagina en 100 aunque se pida mas.
        self.tam_pagina = min(int(self.config.get("tam_pagina", 100)), 100)
        self.max_paginas = int(self.config.get("max_paginas", 50))
        # Si el cliente ya tiene un sitio con un proxy a NOCNOK (como
        # /api/propiedades en el de RedNorte), se le apunta ahi y no hace
        # falta que entregue su llave de API: el proxy la guarda del lado
        # del servidor. Un obstaculo menos para cerrar la venta.
        self.proxy_url = self.config.get("proxy_url", "")
        # Plantilla para armar el link publico de cada ficha, p. ej.
        # "https://www.rednorte.mx/propiedad/{codigo}". Sin esto el agente
        # puede describir la propiedad pero no mandar a donde verla.
        self.url_ficha = self.config.get("url_ficha", "")

    @property
    def llave(self) -> str:
        valor = os.environ.get(self.var_llave, "")
        if not valor:
            raise ErrorInventario(
                f"Falta la variable de entorno {self.var_llave}. "
                f"Ponla en el archivo .env antes de arrancar el servidor."
            )
        return valor

    async def _descargar(self) -> list[Propiedad]:
        """Baja el inventario completo, pagina por pagina.

        OJO con 'pageCount': NO es el numero de paginas, es un espejo de
        'pageSize' (pides 20 y responde 20; pides 100 y responde 100). Usarlo
        como total de paginas -que es lo que parece- deja el inventario
        truncado en silencio, sin ningun error. Las paginas reales se sacan
        de 'totalItems'.
        """
        items: list[dict] = []
        async with httpx.AsyncClient(timeout=40.0) as cliente:
            pagina = 1
            total_paginas = self.max_paginas
            while pagina <= total_paginas and pagina <= self.max_paginas:
                datos = await self._pedir_pagina(cliente, pagina)
                lote = datos.get("items") or []
                items.extend(lote)

                total = datos.get("totalItems")
                if total:
                    total_paginas = -(-int(total) // self.tam_pagina)  # techo

                # Cinturon y tirantes: si la pagina vino incompleta o vacia,
                # ya no hay mas, sin importar lo que digan los contadores.
                if len(lote) < self.tam_pagina:
                    break
                pagina += 1

        if not items:
            raise ErrorInventario("El inventario respondio vacio.")
        return [self._mapear(x) for x in items]

    async def _pedir_pagina(self, cliente: httpx.AsyncClient, pagina: int) -> dict:
        cuerpo = {"pageNumber": pagina, "pageSize": self.tam_pagina,
                  "sortBy": "CreatedAt", "sortDirection": "Descending"}

        if self.proxy_url:
            url, cabeceras = self.proxy_url, {"Content-Type": "application/json"}
        else:
            url = URL_BUSQUEDA
            cabeceras = {"Content-Type": "application/json", "x-api-key": self.llave}

        r = await cliente.post(url, headers=cabeceras, json=cuerpo)
        if r.status_code != 200:
            fuente = "El proxy" if self.proxy_url else "NOCNOK"
            raise ErrorInventario(
                f"{fuente} respondio {r.status_code}: {r.text[:200]}")
        return (r.json() or {}).get("data") or {}

    def _mapear(self, p: dict) -> Propiedad:
        es_renta = bool(p.get("isRent")) and not p.get("isSale")
        precio = p.get("rentPrice") if es_renta else p.get("salePrice")

        if precio is not None:
            texto = f"${round(float(precio)):,}" + ("/mes" if es_renta else "")
        else:
            texto = (p.get("salePriceText") or p.get("rentPriceText")
                     or "Precio a consultar")

        codigo = p.get("code") or ""
        url = ""
        if self.url_ficha and codigo:
            url = self.url_ficha.format(codigo=codigo.lower())

        return Propiedad(
            id=codigo,
            titulo=p.get("title") or TIPO.get(p.get("type"), "Propiedad"),
            tipo=TIPO.get(p.get("type"), "Casa"),
            categoria=CATEGORIA.get(p.get("category"), "Residencial"),
            operacion="Renta" if es_renta else "Venta",
            precio=float(precio or 0),
            precio_texto=texto,
            moneda=p.get("currency") or "MXN",
            colonia=p.get("settlement") or "",
            municipio=p.get("municipality") or p.get("county") or "",
            estado=p.get("state") or "",
            recamaras=int(p.get("bedrooms") or 0),
            banos=float(p.get("bathrooms") or 0),
            estacionamientos=int(p.get("parkingSpaces") or 0),
            construccion_m2=float(p.get("constructionSize") or 0),
            terreno_m2=float(p.get("lotSize") or 0),
            descripcion=p.get("description") or "",
            amenidades=[str(f) for f in (p.get("features") or [])],
            imagenes=[str(u) for u in (p.get("pictureUrls") or [])],
            url=url,
        )
